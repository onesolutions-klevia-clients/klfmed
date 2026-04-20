import logging

from odoo import models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        """Override to auto-create/update draft invoices on dropship confirmation."""
        res = super().button_validate()
        for picking in self:
            _logger.info(
                '[button_validate] Picking %s | picking_type_code=%s state=%s',
                picking.name, picking.picking_type_code, picking.state,
            )
            if picking.picking_type_code == 'dropship' and picking.state == 'done':
                picking._auto_invoice_dropship()
            elif picking.picking_type_code == 'dropship':
                _logger.info('[button_validate] Picking %s | dropship but state=%s, skipping auto-invoice', picking.name, picking.state)
            else:
                _logger.info('[button_validate] Picking %s | not a dropship, skipping auto-invoice', picking.name)
        return res

    def _auto_invoice_dropship(self):
        """
        Auto-create or update a draft vendor bill when a dropship is confirmed.

        Business rules:
        - Match key: Invoice Number (x_studio_invoice_number) only
        - If a draft bill exists for the same invoice number, append lines to it
        - If no draft exists (or all are posted/cancelled), create a new one
        - Document remains in draft for manual validation
        - Customer invoices are NOT auto-created; they must be created via SO wizard.
        """
        self.ensure_one()

        invoice_number = self.x_studio_invoice_number or ''
        _logger.info('Dropship %s: _auto_invoice_dropship called, invoice_number="%s"', self.name, invoice_number)

        # --- Vendor bill (in_invoice) ---
        purchase_order = self._get_dropship_purchase_order()
        if not purchase_order:
            _logger.warning('Dropship %s: no related Purchase Order found, skipping vendor bill.', self.name)
            return

        _logger.info('Dropship %s: found PO %s, searching for draft vendor bill with invoice_number="%s"',
                     self.name, purchase_order.name, invoice_number)

        draft_bill = self._find_draft_vendor_bill(purchase_order, invoice_number)
        if draft_bill:
            _logger.info('Dropship %s: appending lines to existing draft vendor bill %s', self.name, draft_bill.name)
            bill_lines_before = len(draft_bill.invoice_line_ids)
            self._append_vendor_bill_lines(draft_bill)
            _logger.info('Dropship %s: vendor bill %s lines: %d → %d',
                         self.name, draft_bill.name, bill_lines_before, len(draft_bill.invoice_line_ids))
        else:
            _logger.info('Dropship %s: creating new draft vendor bill for PO %s', self.name, purchase_order.name)
            bill = self._create_draft_vendor_bill(purchase_order, invoice_number)
            _logger.info('Dropship %s: created vendor bill %s with %d lines',
                         self.name, bill.name, len(bill.invoice_line_ids))

    def _get_dropship_purchase_order(self):
        """Retrieve the Purchase Order linked to this dropship picking."""
        self.ensure_one()
        for move in self.move_ids:
            if move.purchase_line_id and move.purchase_line_id.order_id:
                return move.purchase_line_id.order_id
        return None

    def _find_draft_vendor_bill(self, purchase_order, invoice_number):
        """
        Search for an existing draft vendor bill matching the invoice number.
        The match key is invoice number only (not PO name), so that multiple
        dropships with the same invoice number consolidate into a single bill.
        Returns the draft bill if found, or None.
        """
        if not invoice_number:
            return None
        domain = [
            ('state', '=', 'draft'),
            ('move_type', '=', 'in_invoice'),
            ('x_studio_invoice_number', '=', invoice_number),
        ]
        return self.env['account.move'].search(domain, limit=1) or None

    def _create_draft_vendor_bill(self, purchase_order, invoice_number):
        """Create a new draft vendor bill with lines from this dropship."""
        bill_vals = {
            'move_type': 'in_invoice',
            'partner_id': purchase_order.partner_id.id,
            'invoice_origin': purchase_order.name,
            'x_studio_invoice_number': invoice_number,
            'currency_id': purchase_order.currency_id.id,
            'invoice_line_ids': self._prepare_vendor_bill_lines(),
        }
        bill = self.env['account.move'].create(bill_vals)
        _logger.info(
            'Dropship %s: created draft vendor bill %s',
            self.name, bill.name
        )
        return bill

    def _append_vendor_bill_lines(self, bill):
        """Append new lines to an existing draft vendor bill without modifying existing lines."""
        new_lines = self._prepare_vendor_bill_lines()
        if new_lines:
            bill.write({'invoice_line_ids': new_lines})

    def _prepare_vendor_bill_lines(self):
        """
        Prepare vendor bill line values from the dropship picking moves.
        One bill line is created per lot number (stock.move.line).
        If no lots are tracked, falls back to one line per purchase line.

        Lots are aggregated across ALL moves for the same purchase line to avoid
        creating duplicate lines when a multi-step route produces multiple stock.moves
        for the same PO line in a single picking.
        """
        _logger.info(
            '[_prepare_vendor_bill_lines] Picking %s | %d move(s) to process',
            self.name, len(self.move_ids),
        )

        # Group moves by purchase_line (or product as fallback) to aggregate lots across
        # all moves for the same line — prevents x-duplication in multi-step routes.
        per_line = {}  # key -> {common_vals, lot_quantities, total_qty}

        for move in self.move_ids:
            if move.state == 'cancel':
                _logger.info('[_prepare_vendor_bill_lines] Move %s skipped (cancelled)', move.id)
                continue

            purchase_line = move.purchase_line_id
            group_key = purchase_line.id if purchase_line else ('product', move.product_id.id)

            _logger.info(
                '[_prepare_vendor_bill_lines] Move %s | product=%s purchase_line=%s group_key=%s move_lines=%d',
                move.id, move.product_id.display_name,
                purchase_line.id if purchase_line else 'none',
                group_key, len(move.move_line_ids),
            )

            if group_key not in per_line:
                price_unit = purchase_line.price_unit if purchase_line else move.product_id.standard_price
                common_vals = {
                    'product_id': move.product_id.id,
                    'price_unit': price_unit,
                    'name': move.description_picking or move.product_id.display_name,
                }
                if purchase_line and purchase_line.tax_ids:
                    common_vals['tax_ids'] = [(6, 0, purchase_line.tax_ids.ids)]
                if move.x_studio_po_no:
                    common_vals['x_studio_po_no_ref'] = move.x_studio_po_no
                if move.x_studio_delivery_date:
                    common_vals['x_studio_delivery_date'] = move.x_studio_delivery_date
                if purchase_line:
                    common_vals['purchase_line_id'] = purchase_line.id
                per_line[group_key] = {
                    'common_vals': common_vals,
                    'lot_quantities': {},
                    'total_qty': 0.0,
                }
                _logger.info(
                    '[_prepare_vendor_bill_lines] New group key=%s | price_unit=%.2f',
                    group_key, price_unit,
                )
            else:
                _logger.info(
                    '[_prepare_vendor_bill_lines] Move %s merged into existing group key=%s',
                    move.id, group_key,
                )

            data = per_line[group_key]
            for move_line in move.move_line_ids:
                if move_line.qty_done <= 0:
                    continue
                lot_key = move_line.lot_id.id if move_line.lot_id else False
                lq = data['lot_quantities']
                if lot_key not in lq:
                    lq[lot_key] = {'qty': 0.0, 'lot': move_line.lot_id}
                lq[lot_key]['qty'] += move_line.qty_done
                _logger.info(
                    '[_prepare_vendor_bill_lines] Move %s | lot "%s" qty_done=%.2f → group total=%.2f',
                    move.id,
                    move_line.lot_id.name if move_line.lot_id else 'no-lot',
                    move_line.qty_done, lq[lot_key]['qty'],
                )
            data['total_qty'] += move.quantity

        lines = []
        for group_key, data in per_line.items():
            common_vals = data['common_vals']
            lot_quantities = data['lot_quantities']

            if False in lot_quantities and len(lot_quantities) > 1:
                _logger.info('[_prepare_vendor_bill_lines] group %s | dropping no-lot aggregate entry', group_key)
                del lot_quantities[False]

            _logger.info(
                '[_prepare_vendor_bill_lines] group %s | %d distinct lot(s), total_qty=%.2f',
                group_key, len(lot_quantities), data['total_qty'],
            )

            if lot_quantities:
                for entry in lot_quantities.values():
                    line_vals = dict(common_vals)
                    line_vals['quantity'] = entry['qty']
                    lot = entry['lot']
                    if lot:
                        line_vals['x_studio_lot_number'] = lot.name
                        if lot.expiration_date:
                            exp_date = lot.expiration_date.date() if hasattr(lot.expiration_date, 'date') else lot.expiration_date
                            line_vals['x_studio_expiration_date'] = exp_date
                    _logger.info(
                        '[_prepare_vendor_bill_lines] → bill line: lot="%s" qty=%.2f',
                        lot.name if lot else 'no-lot', entry['qty'],
                    )
                    lines.append((0, 0, line_vals))
            elif data['total_qty'] > 0:
                line_vals = dict(common_vals)
                line_vals['quantity'] = data['total_qty']
                _logger.info(
                    '[_prepare_vendor_bill_lines] → bill line (no lots): qty=%.2f', data['total_qty'],
                )
                lines.append((0, 0, line_vals))

        _logger.info(
            '[_prepare_vendor_bill_lines] Picking %s | result: %d bill line(s)',
            self.name, len(lines),
        )
        return lines

    def _get_dropship_sale_order(self):
        """Retrieve the Sales Order linked to this dropship picking."""
        self.ensure_one()

        # Dropship pickings are linked via PO → SO
        # The purchase order's origin field contains the SO name
        for move in self.move_ids:
            if move.purchase_line_id and move.purchase_line_id.order_id.origin:
                sale_order = self.env['sale.order'].search([
                    ('name', '=', move.purchase_line_id.order_id.origin)
                ], limit=1)
                if sale_order:
                    return sale_order
        return None

    def _find_draft_invoice(self, sale_order, invoice_number):
        """
        Search for an existing draft invoice matching the SO + invoice number.
        Returns the draft invoice if found, or None.
        """
        domain = [
            ('state', '=', 'draft'),
            ('move_type', '=', 'out_invoice'),
            ('invoice_origin', '=', sale_order.name),
            ('x_studio_invoice_number', '=', invoice_number),
        ]
        return self.env['account.move'].search(domain, limit=1) or None

    def _create_draft_invoice(self, sale_order, invoice_number):
        """Create a new draft invoice with lines from this dropship."""
        invoice_vals = {
            'move_type': 'out_invoice',
            'partner_id': sale_order.partner_invoice_id.id or sale_order.partner_id.id,
            'invoice_origin': sale_order.name,
            'x_studio_invoice_number': invoice_number,
            'currency_id': sale_order.currency_id.id,
            'invoice_line_ids': self._prepare_invoice_lines(sale_order),
        }

        invoice = self.env['account.move'].create(invoice_vals)
        _logger.info(
            'Dropship %s: created draft invoice %s',
            self.name, invoice.name
        )
        return invoice

    def _append_invoice_lines(self, invoice, sale_order):
        """Append new lines to an existing draft invoice without modifying existing lines."""
        new_lines = self._prepare_invoice_lines(sale_order)
        if new_lines:
            invoice.write({
                'invoice_line_ids': new_lines,
            })

    def _prepare_invoice_lines(self, sale_order):
        """
        Prepare invoice line values from the dropship picking moves.
        One invoice line is created per lot number (stock.move.line).
        If no lots are tracked, falls back to one line per sale line.

        Lots are aggregated across ALL moves for the same sale line to avoid
        creating duplicate lines when a multi-step route produces multiple stock.moves
        for the same SO line in a single picking.
        """
        _logger.info(
            '[_prepare_invoice_lines] Picking %s | SO=%s | %d move(s)',
            self.name, sale_order.name, len(self.move_ids),
        )

        # Group moves by sale_line (or product as fallback) to aggregate lots across
        # all moves for the same line — prevents x-duplication in multi-step routes.
        per_line = {}  # key -> {common_vals, lot_quantities, total_qty}

        for move in self.move_ids:
            if move.state == 'cancel':
                _logger.info('[_prepare_invoice_lines] Move %s skipped (cancelled)', move.id)
                continue

            sale_line = self._find_sale_line(move, sale_order)
            group_key = sale_line.id if sale_line else ('product', move.product_id.id)

            _logger.info(
                '[_prepare_invoice_lines] Move %s | product=%s sale_line=%s group_key=%s move_lines=%d',
                move.id, move.product_id.display_name,
                sale_line.id if sale_line else 'none',
                group_key, len(move.move_line_ids),
            )

            if group_key not in per_line:
                price_unit = sale_line.price_unit if sale_line else move.product_id.lst_price
                common_vals = {
                    'product_id': move.product_id.id,
                    'price_unit': price_unit,
                    'name': move.description_picking or move.product_id.display_name,
                }
                if sale_line and sale_line.tax_ids:
                    common_vals['tax_ids'] = [(6, 0, sale_line.tax_ids.ids)]
                if move.x_studio_po_no:
                    common_vals['x_studio_po_no_ref'] = move.x_studio_po_no
                if move.x_studio_delivery_date:
                    common_vals['x_studio_delivery_date'] = move.x_studio_delivery_date
                if sale_line:
                    common_vals['sale_line_ids'] = [(4, sale_line.id)]
                per_line[group_key] = {
                    'common_vals': common_vals,
                    'lot_quantities': {},
                    'total_qty': 0.0,
                }
                _logger.info(
                    '[_prepare_invoice_lines] New group key=%s | price_unit=%.2f',
                    group_key, price_unit,
                )
            else:
                _logger.info(
                    '[_prepare_invoice_lines] Move %s merged into existing group key=%s',
                    move.id, group_key,
                )

            data = per_line[group_key]
            for move_line in move.move_line_ids:
                if move_line.qty_done <= 0:
                    continue
                lot_key = move_line.lot_id.id if move_line.lot_id else False
                lq = data['lot_quantities']
                if lot_key not in lq:
                    lq[lot_key] = {'qty': 0.0, 'lot': move_line.lot_id}
                lq[lot_key]['qty'] += move_line.qty_done
                _logger.info(
                    '[_prepare_invoice_lines] Move %s | lot "%s" qty_done=%.2f → group total=%.2f',
                    move.id,
                    move_line.lot_id.name if move_line.lot_id else 'no-lot',
                    move_line.qty_done, lq[lot_key]['qty'],
                )
            data['total_qty'] += move.quantity

        lines = []
        for group_key, data in per_line.items():
            common_vals = data['common_vals']
            lot_quantities = data['lot_quantities']

            if False in lot_quantities and len(lot_quantities) > 1:
                _logger.info('[_prepare_invoice_lines] group %s | dropping no-lot aggregate entry', group_key)
                del lot_quantities[False]

            _logger.info(
                '[_prepare_invoice_lines] group %s | %d distinct lot(s), total_qty=%.2f',
                group_key, len(lot_quantities), data['total_qty'],
            )

            if lot_quantities:
                for entry in lot_quantities.values():
                    line_vals = dict(common_vals)
                    line_vals['quantity'] = entry['qty']
                    lot = entry['lot']
                    if lot:
                        line_vals['x_studio_lot_number'] = lot.name
                        if lot.expiration_date:
                            exp_date = lot.expiration_date.date() if hasattr(lot.expiration_date, 'date') else lot.expiration_date
                            line_vals['x_studio_expiration_date'] = exp_date
                    _logger.info(
                        '[_prepare_invoice_lines] → invoice line: lot="%s" qty=%.2f',
                        lot.name if lot else 'no-lot', entry['qty'],
                    )
                    lines.append((0, 0, line_vals))
            elif data['total_qty'] > 0:
                line_vals = dict(common_vals)
                line_vals['quantity'] = data['total_qty']
                _logger.info(
                    '[_prepare_invoice_lines] → invoice line (no lots): qty=%.2f', data['total_qty'],
                )
                lines.append((0, 0, line_vals))

        _logger.info(
            '[_prepare_invoice_lines] Picking %s | result: %d invoice line(s)',
            self.name, len(lines),
        )
        return lines

    def _find_sale_line(self, stock_move, sale_order):
        """Find the matching SO line for a stock move (by product and PO line link)."""
        # Via purchase line → sale line link
        if stock_move.purchase_line_id and stock_move.purchase_line_id.sale_line_id:
            return stock_move.purchase_line_id.sale_line_id

        # Fallback: match by product on the SO
        for line in sale_order.order_line:
            if line.product_id == stock_move.product_id:
                return line

        return None
