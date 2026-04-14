import logging

from odoo import models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        """Override to auto-create/update draft invoices on dropship confirmation."""
        res = super().button_validate()
        for picking in self:
            if picking.picking_type_code == 'dropship':
                picking._auto_invoice_dropship()
        return res

    def _auto_invoice_dropship(self):
        """
        Auto-create or update a draft customer invoice and vendor bill when a dropship is confirmed.

        Business rules:
        - Match key: Sales Order ID + Invoice Number (x_studio_invoice_number)
        - If a draft invoice/bill exists for the same key, append lines to it
        - If no draft exists (or all are posted/cancelled), create a new one
        - Documents remain in draft for manual validation
        """
        self.ensure_one()

        sale_order = self._get_dropship_sale_order()
        if not sale_order:
            _logger.warning(
                'Dropship %s: no related Sales Order found, skipping auto-invoice.',
                self.name
            )
            return

        invoice_number = self.x_studio_invoice_number or ''

        # --- Customer invoice (out_invoice) ---
        draft_invoice = self._find_draft_invoice(sale_order, invoice_number)
        if draft_invoice:
            _logger.info(
                'Dropship %s: appending lines to existing draft invoice %s',
                self.name, draft_invoice.name
            )
            self._append_invoice_lines(draft_invoice, sale_order)
        else:
            _logger.info(
                'Dropship %s: creating new draft invoice for SO %s',
                self.name, sale_order.name
            )
            self._create_draft_invoice(sale_order, invoice_number)

        # --- Vendor bill (in_invoice) ---
        purchase_order = self._get_dropship_purchase_order()
        if not purchase_order:
            _logger.warning(
                'Dropship %s: no related Purchase Order found, skipping vendor bill.',
                self.name
            )
            return

        draft_bill = self._find_draft_vendor_bill(purchase_order, invoice_number)
        if draft_bill:
            _logger.info(
                'Dropship %s: appending lines to existing draft vendor bill %s',
                self.name, draft_bill.name
            )
            self._append_vendor_bill_lines(draft_bill)
        else:
            _logger.info(
                'Dropship %s: creating new draft vendor bill for PO %s',
                self.name, purchase_order.name
            )
            self._create_draft_vendor_bill(purchase_order, invoice_number)

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
        If no lots are tracked, falls back to one line per move.
        """
        lines = []
        for move in self.move_ids:
            if move.state == 'cancel':
                continue

            purchase_line = move.purchase_line_id
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

            # Group qty_done by lot
            lot_quantities = {}
            for move_line in move.move_line_ids:
                if move_line.qty_done <= 0:
                    continue
                key = move_line.lot_id.id if move_line.lot_id else False
                if key not in lot_quantities:
                    lot_quantities[key] = {'qty': 0.0, 'lot': move_line.lot_id}
                lot_quantities[key]['qty'] += move_line.qty_done

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
                    lines.append((0, 0, line_vals))
            elif move.quantity > 0 and not any(ml.qty_done > 0 for ml in move.move_line_ids):
                # Fallback: product without lot tracking, only if no sibling move covers it with lots
                sibling_has_lots = any(
                    ml.qty_done > 0 and other.product_id == move.product_id
                    for other in self.move_ids if other != move
                    for ml in other.move_line_ids
                )
                if not sibling_has_lots:
                    line_vals = dict(common_vals)
                    line_vals['quantity'] = move.quantity
                    lines.append((0, 0, line_vals))

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
        If no lots are tracked, falls back to one line per move.
        """
        lines = []
        for move in self.move_ids:
            if move.state == 'cancel':
                continue

            # Find the matching SO line for pricing
            sale_line = self._find_sale_line(move, sale_order)
            price_unit = sale_line.price_unit if sale_line else move.product_id.lst_price

            # Fields shared across all lines for this move
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

            # Group qty_done by lot (lot_id=False means no lot tracking)
            lot_quantities = {}
            for move_line in move.move_line_ids:
                if move_line.qty_done <= 0:
                    continue
                key = move_line.lot_id.id if move_line.lot_id else False
                if key not in lot_quantities:
                    lot_quantities[key] = {'qty': 0.0, 'lot': move_line.lot_id}
                lot_quantities[key]['qty'] += move_line.qty_done

            if lot_quantities:
                # One invoice line per lot
                for entry in lot_quantities.values():
                    line_vals = dict(common_vals)
                    line_vals['quantity'] = entry['qty']
                    lot = entry['lot']
                    if lot:
                        line_vals['x_studio_lot_number'] = lot.name
                        if lot.expiration_date:
                            exp_date = lot.expiration_date.date() if hasattr(lot.expiration_date, 'date') else lot.expiration_date
                            line_vals['x_studio_expiration_date'] = exp_date
                    lines.append((0, 0, line_vals))
            elif move.quantity > 0 and not any(ml.qty_done > 0 for ml in move.move_line_ids):
                # Fallback: product without lot tracking, only if no sibling move covers it with lots
                sibling_has_lots = any(
                    ml.qty_done > 0 and other.product_id == move.product_id
                    for other in self.move_ids if other != move
                    for ml in other.move_line_ids
                )
                if not sibling_has_lots:
                    line_vals = dict(common_vals)
                    line_vals['quantity'] = move.quantity
                    lines.append((0, 0, line_vals))

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
