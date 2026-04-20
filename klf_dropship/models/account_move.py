import logging

from odoo import models, api, fields

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-populate logistics fields and split lot lines after creation."""
        _logger.info('[AccountMove.create] Creating %d invoice(s)', len(vals_list))
        moves = super().create(vals_list)
        for move in moves:
            _logger.info(
                '[AccountMove.create] Post-create hooks for invoice %s (type=%s, state=%s, lines=%d)',
                move.name or move.id, move.move_type, move.state, len(move.invoice_line_ids),
            )
            move._populate_from_picking()
            move._split_lines_by_lot()
        return moves

    def write(self, vals):
        """Re-populate logistics fields and split lot lines when invoice lines change."""
        has_line_change = 'invoice_line_ids' in vals or 'line_ids' in vals
        skip_populate = self.env.context.get('skip_populate_from_picking')
        skip_split = self.env.context.get('skip_split_lines')

        if has_line_change:
            _logger.info(
                '[AccountMove.write] invoice_line_ids/line_ids changed on %d invoice(s) '
                '| skip_populate=%s skip_split=%s',
                len(self), skip_populate, skip_split,
            )

        res = super().write(vals)

        if has_line_change:
            for move in self:
                _logger.info(
                    '[AccountMove.write] Post-write hooks for invoice %s (lines now=%d)',
                    move.name or move.id, len(move.invoice_line_ids),
                )
                if not skip_populate:
                    move._populate_from_picking()
                else:
                    _logger.info('[AccountMove.write] skip_populate_from_picking=True → skipping')
                if not skip_split:
                    move._split_lines_by_lot()
                else:
                    _logger.info('[AccountMove.write] skip_split_lines=True → skipping')
        return res

    def _populate_from_picking(self):
        """
        Populate logistics fields from related stock picking and customer defaults.
        This is called at invoice creation to source fields from the DS.

        Fields populated:
        - x_studio_port_of_destination (from customer default or picking)
        - x_studio_port_of_loading (from picking)
        - x_studio_invoice_number (from picking)
        - x_studio_destination_country (from partner country)
        """
        for move in self:
            _logger.info(
                '[_populate_from_picking] Invoice %s | invoice_number_before="%s"',
                move.name or move.id, move.x_studio_invoice_number or '',
            )
            # Find related picking and sale order from invoice lines
            pickings = self.env['stock.picking']
            sale_orders = self.env['sale.order']
            for line in move.invoice_line_ids:
                if line.sale_line_ids:
                    for sale_line in line.sale_line_ids:
                        pickings |= sale_line.move_ids.mapped('picking_id')
                        sale_orders |= sale_line.order_id
                if line.purchase_line_id:
                    pickings |= line.purchase_line_id.move_ids.mapped('picking_id')
                    # Find SO from PO origin
                    if line.purchase_line_id.order_id.origin:
                        so = self.env['sale.order'].search([
                            ('name', '=', line.purchase_line_id.order_id.origin)
                        ], limit=1)
                        if so:
                            sale_orders |= so

            _logger.info(
                '[_populate_from_picking] Invoice %s | found %d picking(s): %s | found %d SO(s): %s',
                move.name or move.id,
                len(pickings), pickings.mapped('name'),
                len(sale_orders), sale_orders.mapped('name'),
            )

            # Source defaults from customer (via SO partner, fallback to invoice partner)
            partner = sale_orders[0].partner_id if sale_orders else move.partner_id
            if partner:
                # Port of destination from customer default
                if not move.x_studio_port_of_destination and partner.x_studio_default_destination_port:
                    move.x_studio_port_of_destination = partner.x_studio_default_destination_port
                # Destination country from customer country
                if not move.x_studio_destination_country and partner.country_id:
                    move.x_studio_destination_country = partner.country_id.id
            # Incoterm from SO
            if sale_orders:
                if not move.invoice_incoterm_id and sale_orders[0].incoterm:
                    move.invoice_incoterm_id = sale_orders[0].incoterm

            # Get data from pickings
            for picking in pickings:
                if picking.x_studio_port_of_destination and not move.x_studio_port_of_destination:
                    move.x_studio_port_of_destination = picking.x_studio_port_of_destination
                if picking.x_studio_port_of_loading and not move.x_studio_port_of_loading:
                    move.x_studio_port_of_loading = picking.x_studio_port_of_loading
                if picking.x_studio_invoice_number and not move.x_studio_invoice_number:
                    _logger.info(
                        '[_populate_from_picking] Invoice %s | setting invoice_number="%s" from picking %s',
                        move.name or move.id, picking.x_studio_invoice_number, picking.name,
                    )
                    move.x_studio_invoice_number = picking.x_studio_invoice_number
                if picking.partner_id and picking.partner_id.country_id and not move.x_studio_destination_country:
                    move.x_studio_destination_country = picking.partner_id.country_id.id

            _logger.info(
                '[_populate_from_picking] Invoice %s | invoice_number_after="%s"',
                move.name or move.id, move.x_studio_invoice_number or '',
            )

    def _split_lines_by_lot(self):
        """
        Split aggregated invoice lines into one line per lot.

        Handles the SO wizard path where Odoo creates one line per SO line with
        total quantity. We split these into per-lot lines matching the lot-level
        granularity required for dropship tracking.

        Detection logic:
        - Lines from button_validate path already have x_studio_lot_number set to a
          single lot name (no comma) → skipped.
        - Lines from SO wizard path have x_studio_lot_number set by _populate_lot_number()
          to "lot1, lot2, ..." (with comma) → split into per-lot lines.
        - Safety: total lot qty must match the invoice line qty to prevent over-split
          in partial invoicing scenarios.

        Scoping: lot quantities are restricted to pickings matching this invoice's
        x_studio_invoice_number, preventing lots from other shipments (backorders,
        multi-step routes) from inflating the line count or breaking the safety check.
        """
        for invoice in self:
            _logger.info(
                '[_split_lines_by_lot] Invoice %s | state=%s type=%s',
                invoice.name or invoice.id, invoice.state, invoice.move_type,
            )

            if invoice.state != 'draft':
                _logger.info('[_split_lines_by_lot] Invoice %s | skipped (not draft)', invoice.name or invoice.id)
                continue
            if invoice.move_type not in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
                _logger.info('[_split_lines_by_lot] Invoice %s | skipped (move_type=%s)', invoice.name or invoice.id, invoice.move_type)
                continue

            # Find relevant pickings for this invoice via invoice number to avoid
            # counting lots from unrelated shipments (other backorders, multi-step moves).
            relevant_picking_ids = set()
            if invoice.x_studio_invoice_number:
                relevant_pickings = self.env['stock.picking'].search([
                    ('x_studio_invoice_number', '=', invoice.x_studio_invoice_number),
                    ('state', '=', 'done'),
                ])
                relevant_picking_ids = set(relevant_pickings.ids)
                _logger.info(
                    '[_split_lines_by_lot] Invoice %s | invoice_number="%s" → %d relevant picking(s): %s',
                    invoice.name or invoice.id, invoice.x_studio_invoice_number,
                    len(relevant_pickings), relevant_pickings.mapped('name'),
                )
            else:
                _logger.info(
                    '[_split_lines_by_lot] Invoice %s | no invoice_number set → no picking filter (all moves considered)',
                    invoice.name or invoice.id,
                )

            to_remove_ids = []
            to_add_vals = []

            # Query lines directly from DB to avoid One2many cache issues right after create()
            lines = self.env['account.move.line'].search([
                ('move_id', '=', invoice.id),
                ('display_type', '=', 'product'),
                ('product_id', '!=', False),
            ])

            _logger.info(
                '[_split_lines_by_lot] Invoice %s | %d product line(s) to inspect',
                invoice.name or invoice.id, len(lines),
            )

            for line in lines:
                lot_field = line.x_studio_lot_number or ''
                _logger.info(
                    '[_split_lines_by_lot] Line %s | product=%s qty=%.2f lot_field="%s"',
                    line.id, line.product_id.display_name, line.quantity, lot_field,
                )

                # Lines already split to a single lot have no comma in their lot field
                if lot_field and ',' not in lot_field:
                    _logger.info('[_split_lines_by_lot] Line %s | skipped (single lot, no comma)', line.id)
                    continue

                # Lines with no lot info at all: nothing to split
                if not lot_field:
                    _logger.info('[_split_lines_by_lot] Line %s | skipped (no lot number set)', line.id)
                    continue

                # Collect lot quantities from linked stock moves, restricted to relevant pickings.
                # Use qty_done if the picking is validated, reserved qty otherwise
                # (covers both "ordered qty" invoicing policy and pre-assigned lots).
                lot_quantities = {}

                def _add_move_lots(move):
                    if move.state == 'cancel':
                        _logger.info(
                            '[_split_lines_by_lot] Line %s | move %s skipped (cancelled)',
                            line.id, move.id,
                        )
                        return
                    if relevant_picking_ids and move.picking_id.id not in relevant_picking_ids:
                        _logger.info(
                            '[_split_lines_by_lot] Line %s | move %s picking %s skipped (not in relevant pickings)',
                            line.id, move.id, move.picking_id.name if move.picking_id else 'none',
                        )
                        return
                    _logger.info(
                        '[_split_lines_by_lot] Line %s | processing move %s (picking=%s state=%s move_lines=%d)',
                        line.id, move.id,
                        move.picking_id.name if move.picking_id else 'none',
                        move.state, len(move.move_line_ids),
                    )
                    for ml in move.move_line_ids:
                        if not ml.lot_id:
                            continue
                        qty = ml.qty_done or (
                            getattr(ml, 'reserved_uom_qty', 0.0)
                            or getattr(ml, 'product_uom_qty', 0.0)
                        )
                        if qty <= 0:
                            continue
                        key = ml.lot_id.id
                        if key not in lot_quantities:
                            lot_quantities[key] = {'qty': 0.0, 'lot': ml.lot_id}
                        lot_quantities[key]['qty'] += qty
                        _logger.info(
                            '[_split_lines_by_lot] Line %s | lot "%s" qty_done=%.2f → running total=%.2f',
                            line.id, ml.lot_id.name, qty, lot_quantities[key]['qty'],
                        )

                if line.sale_line_ids:
                    _logger.info(
                        '[_split_lines_by_lot] Line %s | has %d sale_line(s), iterating moves',
                        line.id, len(line.sale_line_ids),
                    )
                    for sale_line in line.sale_line_ids:
                        _logger.info(
                            '[_split_lines_by_lot] Line %s | sale_line %s has %d move(s)',
                            line.id, sale_line.id, len(sale_line.move_ids),
                        )
                        for move in sale_line.move_ids:
                            _add_move_lots(move)

                elif line.purchase_line_id:
                    _logger.info(
                        '[_split_lines_by_lot] Line %s | has purchase_line %s with %d move(s)',
                        line.id, line.purchase_line_id.id, len(line.purchase_line_id.move_ids),
                    )
                    for move in line.purchase_line_id.move_ids:
                        _add_move_lots(move)

                else:
                    _logger.info('[_split_lines_by_lot] Line %s | no sale_line_ids and no purchase_line_id', line.id)

                # Drop the no-lot aggregate entry if lot-specific entries exist
                if False in lot_quantities and len(lot_quantities) > 1:
                    _logger.info('[_split_lines_by_lot] Line %s | dropping no-lot aggregate entry', line.id)
                    del lot_quantities[False]

                _logger.info(
                    '[_split_lines_by_lot] Line %s | lot_quantities=%d distinct lots: %s',
                    line.id, len(lot_quantities),
                    {v['lot'].name: v['qty'] for v in lot_quantities.values() if v.get('lot')},
                )

                # Only split if there are multiple distinct lots
                if len(lot_quantities) <= 1:
                    _logger.info('[_split_lines_by_lot] Line %s | skipped (≤1 lot found)', line.id)
                    continue

                # Safety: total lot qty must match invoice line qty.
                # Mismatch means partial invoicing — we can't determine which lots belong here.
                total_lot_qty = sum(entry['qty'] for entry in lot_quantities.values())
                _logger.info(
                    '[_split_lines_by_lot] Line %s | safety check: total_lot_qty=%.2f vs line.quantity=%.2f',
                    line.id, total_lot_qty, line.quantity,
                )
                if abs(total_lot_qty - line.quantity) > 0.001:
                    _logger.warning(
                        '[_split_lines_by_lot] Invoice %s line %s: '
                        'lot qty total %.2f ≠ line qty %.2f → SKIPPING split. '
                        'Lots found: %s',
                        invoice.name, line.id, total_lot_qty, line.quantity,
                        {v['lot'].name: v['qty'] for v in lot_quantities.values() if v.get('lot')},
                    )
                    continue

                _logger.info(
                    '[_split_lines_by_lot] Line %s | WILL SPLIT into %d lot lines',
                    line.id, len(lot_quantities),
                )
                to_remove_ids.append(line.id)

                for entry in lot_quantities.values():
                    vals = {
                        'product_id': line.product_id.id,
                        'name': line.name,
                        'quantity': entry['qty'],
                        'price_unit': line.price_unit,
                        'account_id': line.account_id.id,
                    }
                    if line.tax_ids:
                        vals['tax_ids'] = [(6, 0, line.tax_ids.ids)]
                    if line.sale_line_ids:
                        vals['sale_line_ids'] = [(4, sl.id) for sl in line.sale_line_ids]
                    if line.purchase_line_id:
                        vals['purchase_line_id'] = line.purchase_line_id.id
                    if line.x_studio_po_no_ref:
                        vals['x_studio_po_no_ref'] = line.x_studio_po_no_ref
                    if line.x_studio_delivery_date:
                        vals['x_studio_delivery_date'] = line.x_studio_delivery_date

                    lot = entry.get('lot')
                    if lot:
                        vals['x_studio_lot_number'] = lot.name
                        if lot.expiration_date:
                            exp = lot.expiration_date.date() if hasattr(lot.expiration_date, 'date') else lot.expiration_date
                            vals['x_studio_expiration_date'] = exp
                        _logger.info(
                            '[_split_lines_by_lot] Line %s | new split line: lot="%s" qty=%.2f',
                            line.id, lot.name, entry['qty'],
                        )

                    to_add_vals.append(vals)

            if not to_remove_ids:
                _logger.info('[_split_lines_by_lot] Invoice %s | nothing to split', invoice.name or invoice.id)
                continue

            _logger.info(
                '[_split_lines_by_lot] Invoice %s | removing %d line(s), adding %d lot line(s)',
                invoice.name or invoice.id, len(to_remove_ids), len(to_add_vals),
            )

            write_cmds = [(2, lid) for lid in to_remove_ids]
            write_cmds += [(0, 0, v) for v in to_add_vals]
            invoice.with_context(
                skip_split_lines=True,
                skip_populate_from_picking=True,
            ).write({'invoice_line_ids': write_cmds})

            _logger.info(
                '[_split_lines_by_lot] Invoice %s | split complete, lines now=%d',
                invoice.name or invoice.id, len(invoice.invoice_line_ids),
            )


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-populate x_studio_po_no_ref from the related purchase order origin."""
        _logger.info('[AccountMoveLine.create] Creating %d line(s)', len(vals_list))
        lines = super().create(vals_list)
        for line in lines:
            _logger.info(
                '[AccountMoveLine.create] Line %s | product=%s move=%s lot_field="%s"',
                line.id,
                line.product_id.display_name if line.product_id else 'none',
                line.move_id.name if line.move_id else 'none',
                line.x_studio_lot_number or '',
            )
            line._populate_po_no()
            line._populate_lot_number()
            line._populate_expiration_date()
            line._populate_delivery_date()
            _logger.info(
                '[AccountMoveLine.create] Line %s | after populate: lot="%s"',
                line.id, line.x_studio_lot_number or '',
            )
        return lines

    def _should_apply_pricelist(self):
        """
        Check if pricelist should be applied to this invoice line.

        Returns False if:
        - Not a customer invoice/refund
        - Invoice is posted
        - Invoice was generated from a Sales Order
        - Price was manually edited by user
        """
        self.ensure_one()

        if not self.product_id or not self.move_id:
            return False

        # Only on customer invoices/refunds
        if self.move_id.move_type not in ('out_invoice', 'out_refund'):
            return False

        # Never on posted invoices
        if self.move_id.state == 'posted':
            return False

        # Never on invoices generated from Sales Orders
        if self.move_id.invoice_origin:
            sale_order = self.env['sale.order'].search([
                ('name', '=', self.move_id.invoice_origin)
            ], limit=1)
            if sale_order:
                return False

        # Also check if line is linked to a sale line (another way to detect SO-generated invoices)
        if self.sale_line_ids:
            return False

        # Skip if price was manually edited
        if hasattr(self, 'x_studio_price_manually_set') and self.x_studio_price_manually_set:
            return False

        return True

    def _apply_pricelist_price(self):
        """
        Apply customer's pricelist to calculate and set the unit price.
        Uses the same logic as Sales Orders.

        Fallback: If no pricelist or no matching rule, uses product's standard sales price.
        """
        for line in self:
            if not line._should_apply_pricelist():
                continue

            partner = line.move_id.partner_id
            if not partner:
                continue

            # Get customer's pricelist
            pricelist = getattr(partner, 'property_product_pricelist', None) or getattr(partner, 'pricelist_id', None)

            if pricelist:
                # Calculate price from pricelist using SO-identical logic
                price = pricelist._get_product_price(
                    line.product_id,
                    line.quantity or 1.0,
                    uom=line.product_uom_id,
                    date=line.move_id.invoice_date or line.move_id.date,
                )
            else:
                # Fallback: use product's standard sales price
                price = line.product_id.lst_price

            # Apply the calculated price (with context to avoid marking as manual edit)
            line.with_context(from_pricelist_calculation=True).price_unit = price

    @api.onchange('product_id')
    def _onchange_product_id_apply_pricelist(self):
        """
        Apply customer's pricelist when product is changed.
        Resets the manual edit flag when product changes.
        """
        for line in self:
            # Reset manual edit flag when product changes
            if hasattr(line, 'x_studio_price_manually_set'):
                line.x_studio_price_manually_set = False

        self._apply_pricelist_price()

    @api.onchange('quantity')
    def _onchange_quantity_apply_pricelist(self):
        """
        Recalculate price when quantity changes.
        This is required because pricelist rules can be quantity-dependent.
        """
        self._apply_pricelist_price()

    @api.onchange('price_unit')
    def _onchange_price_unit_mark_manual(self):
        """
        Mark the line as manually edited if user changes the price.
        This prevents automatic recalculation on subsequent changes.

        Note: This requires the x_studio_price_manually_set field to be created
        via Odoo Studio on account.move.line model (Boolean, default False).
        """
        # Only mark as manual if this is a real user edit (not from pricelist calculation)
        # We detect this by checking if we're in a UI context
        if self.env.context.get('from_pricelist_calculation'):
            return

        for line in self:
            if hasattr(line, 'x_studio_price_manually_set') and line.product_id:
                line.x_studio_price_manually_set = True

    def _populate_lot_number(self):
        """
        Populate x_studio_lot_number from related stock move lines (lot/serial numbers).
        Traverses: invoice line → sale/purchase line → stock.move → stock.move.line → lot_id
        """
        for line in self:
            if line.x_studio_lot_number:
                _logger.info(
                    '[_populate_lot_number] Line %s | already has lot="%s", skipping',
                    line.id, line.x_studio_lot_number,
                )
                continue

            lot_names = []

            # From sale lines
            if line.sale_line_ids:
                for sale_line in line.sale_line_ids:
                    for move in sale_line.move_ids:
                        for move_line in move.move_line_ids:
                            if move_line.lot_id and move_line.lot_id.name not in lot_names:
                                lot_names.append(move_line.lot_id.name)

            # From purchase line
            elif line.purchase_line_id:
                for move in line.purchase_line_id.move_ids:
                    for move_line in move.move_line_ids:
                        if move_line.lot_id and move_line.lot_id.name not in lot_names:
                            lot_names.append(move_line.lot_id.name)

            if lot_names:
                result = ', '.join(lot_names)
                _logger.info(
                    '[_populate_lot_number] Line %s | found %d lot(s): "%s"',
                    line.id, len(lot_names), result,
                )
                line.x_studio_lot_number = result
            else:
                _logger.info('[_populate_lot_number] Line %s | no lots found', line.id)

    def _populate_po_no(self):
        """
        Populate x_studio_po_no_ref from related sale order's customer PO number.
        Sources from x_studio_purchase_order_number on the SO header.
        """
        for line in self:
            if line.x_studio_po_no_ref:
                continue

            sale_order = None

            # Try from sale line
            if line.sale_line_ids:
                sale_order = line.sale_line_ids[0].order_id

            # Try from purchase line
            elif line.purchase_line_id and line.purchase_line_id.order_id.origin:
                origin = line.purchase_line_id.order_id.origin
                sale_order = self.env['sale.order'].search([
                    ('name', '=', origin)
                ], limit=1)

            # Fallback: try from invoice origin (SO name on the invoice header)
            if not sale_order and line.move_id.invoice_origin:
                sale_order = self.env['sale.order'].search([
                    ('name', '=', line.move_id.invoice_origin)
                ], limit=1)

            if sale_order and sale_order.x_studio_purchase_order_number:
                line.x_studio_po_no_ref = sale_order.x_studio_purchase_order_number

    def _populate_expiration_date(self):
        """
        Populate x_studio_expiration_date from related stock move lines (lot expiration date).
        Traverses: invoice line → sale/purchase line → stock.move → stock.move.line → lot_id.expiration_date
        """
        user_tz = self.env.user.tz or 'UTC'
        for line in self:
            if line.x_studio_expiration_date:
                continue

            expiration_dates = []

            # From sale lines
            if line.sale_line_ids:
                for sale_line in line.sale_line_ids:
                    for move in sale_line.move_ids:
                        for move_line in move.move_line_ids:
                            if move_line.lot_id and move_line.lot_id.expiration_date:
                                exp_date = fields.Datetime.context_timestamp(line.with_context(tz=user_tz), move_line.lot_id.expiration_date).date() if hasattr(move_line.lot_id.expiration_date, 'date') else move_line.lot_id.expiration_date
                                if exp_date not in expiration_dates:
                                    expiration_dates.append(exp_date)

            # From purchase line
            elif line.purchase_line_id:
                for move in line.purchase_line_id.move_ids:
                    for move_line in move.move_line_ids:
                        if move_line.lot_id and move_line.lot_id.expiration_date:
                            exp_date = fields.Datetime.context_timestamp(line.with_context(tz=user_tz), move_line.lot_id.expiration_date).date() if hasattr(move_line.lot_id.expiration_date, 'date') else move_line.lot_id.expiration_date
                            if exp_date not in expiration_dates:
                                expiration_dates.append(exp_date)

            if expiration_dates:
                # Take the earliest expiration date
                earliest = min(expiration_dates)
                line.x_studio_expiration_date = earliest

    def _populate_delivery_date(self):
        """
        Populate x_studio_delivery_date from related sale or purchase line.
        """
        for line in self:
            if line.x_studio_delivery_date:
                continue

            # From sale line
            if line.sale_line_ids:
                for sale_line in line.sale_line_ids:
                    if sale_line.x_studio_delivery_date:
                        line.x_studio_delivery_date = sale_line.x_studio_delivery_date
                        break

            # From purchase line
            elif line.purchase_line_id and line.purchase_line_id.x_studio_delivery_date:
                line.x_studio_delivery_date = line.purchase_line_id.x_studio_delivery_date
