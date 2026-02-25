import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-populate logistics fields from related stock picking."""
        moves = super().create(vals_list)
        for move in moves:
            move._populate_from_picking()
        return moves

    def write(self, vals):
        """Re-populate logistics fields when invoice lines are modified."""
        res = super().write(vals)
        if 'invoice_line_ids' in vals or 'line_ids' in vals:
            for move in self:
                move._populate_from_picking()
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
                    move.x_studio_invoice_number = picking.x_studio_invoice_number
                if picking.partner_id and picking.partner_id.country_id and not move.x_studio_destination_country:
                    move.x_studio_destination_country = picking.partner_id.country_id.id


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-populate x_studio_po_no_ref from the related purchase order origin."""
        lines = super().create(vals_list)
        for line in lines:
            line._populate_po_no()
            line._populate_lot_number()
            line._populate_expiration_date()
            line._populate_delivery_date()
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
                line.x_studio_lot_number = ', '.join(lot_names)

    def _populate_po_no(self):
        """
        Populate x_studio_po_no_ref from related sale order's customer PO number.
        Sources from x_studio_purchase_order_number on the SO header.
        """
        for line in self:
            _logger.info(
                'PO_NO DEBUG line %s (product=%s): x_studio_po_no_ref=%s, sale_line_ids=%s, purchase_line_id=%s',
                line.id, line.product_id.name, line.x_studio_po_no_ref,
                line.sale_line_ids.ids, line.purchase_line_id.id if line.purchase_line_id else False
            )

            if line.x_studio_po_no_ref:
                continue

            sale_order = None

            # Try from sale line
            if line.sale_line_ids:
                sale_order = line.sale_line_ids[0].order_id
                _logger.info('PO_NO DEBUG: found SO %s from sale_line, x_studio_purchase_order_number=%s',
                             sale_order.name, sale_order.x_studio_purchase_order_number)

            # Try from purchase line
            elif line.purchase_line_id and line.purchase_line_id.order_id.origin:
                origin = line.purchase_line_id.order_id.origin
                sale_order = self.env['sale.order'].search([
                    ('name', '=', origin)
                ], limit=1)
                _logger.info('PO_NO DEBUG: searched SO by origin=%s, found=%s, x_studio_purchase_order_number=%s',
                             origin, sale_order.name if sale_order else None,
                             sale_order.x_studio_purchase_order_number if sale_order else None)

            if sale_order and sale_order.x_studio_purchase_order_number:
                line.x_studio_po_no_ref = sale_order.x_studio_purchase_order_number

    def _populate_expiration_date(self):
        """
        Populate x_studio_expiration_date from related stock move lines (lot expiration date).
        Traverses: invoice line → sale/purchase line → stock.move → stock.move.line → lot_id.expiration_date
        """
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
                                exp_date = move_line.lot_id.expiration_date.date() if hasattr(move_line.lot_id.expiration_date, 'date') else move_line.lot_id.expiration_date
                                if exp_date not in expiration_dates:
                                    expiration_dates.append(exp_date)

            # From purchase line
            elif line.purchase_line_id:
                for move in line.purchase_line_id.move_ids:
                    for move_line in move.move_line_ids:
                        if move_line.lot_id and move_line.lot_id.expiration_date:
                            exp_date = move_line.lot_id.expiration_date.date() if hasattr(move_line.lot_id.expiration_date, 'date') else move_line.lot_id.expiration_date
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
