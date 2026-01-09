from odoo import models, api


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-populate logistics fields from related stock picking."""
        moves = super().create(vals_list)
        for move in moves:
            move._populate_from_picking()
        return moves

    def _populate_from_picking(self):
        """
        Populate logistics fields from related stock picking (Transfer).
        This is called at invoice creation to source fields from the DS.
        
        Fields populated:
        - x_studio_port_of_destination
        - x_studio_port_of_loading
        - x_studio_invoice_number
        - x_studio_destination_country
        """
        for move in self:
            # Find related picking from invoice lines
            pickings = self.env['stock.picking']
            for line in move.invoice_line_ids:
                if line.sale_line_ids:
                    for sale_line in line.sale_line_ids:
                        pickings |= sale_line.move_ids.mapped('picking_id')
                if line.purchase_line_id:
                    pickings |= line.purchase_line_id.move_ids.mapped('picking_id')

            # Get the first picking with data to populate fields
            for picking in pickings:
                if picking.x_studio_port_of_destination and not move.x_studio_port_of_destination:
                    move.x_studio_port_of_destination = picking.x_studio_port_of_destination
                if picking.x_studio_port_of_loading and not move.x_studio_port_of_loading:
                    move.x_studio_port_of_loading = picking.x_studio_port_of_loading
                if picking.x_studio_invoice_number and not move.x_studio_invoice_number:
                    move.x_studio_invoice_number = picking.x_studio_invoice_number
                # Destination country from partner if available
                if picking.partner_id and picking.partner_id.country_id and not move.x_studio_destination_country:
                    move.x_studio_destination_country = picking.partner_id.country_id.id


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-populate x_studio_po_no from the related purchase order origin."""
        lines = super().create(vals_list)
        for line in lines:
            line._populate_po_no()
        return lines

    @api.onchange('product_id')
    def _onchange_product_id_apply_pricelist(self):
        """
        Apply customer's pricelist on invoice lines.
        This replicates SO behavior on invoices as requested by the client.
        
        When a product is selected on an invoice line, if the customer has
        a pricelist defined, the unit price is automatically calculated
        from that pricelist instead of using the standard product price.
        """
        for line in self:
            # Only apply on customer invoices/refunds
            if not line.product_id or not line.move_id:
                continue
            if line.move_id.move_type not in ('out_invoice', 'out_refund'):
                continue

            # Get the customer from the invoice
            partner = line.move_id.partner_id
            if not partner:
                continue

            # Get customer's pricelist TODO: To be verified (Customer or SO)
            pricelist = partner.property_product_pricelist
            if pricelist:
                # Calculate price from pricelist
                price = pricelist._get_product_price(
                    line.product_id,
                    line.quantity or 1.0,
                    uom=line.product_uom_id
                )
                # Apply the calculated price
                line.price_unit = price

    def _populate_po_no(self):
        """
        Populate x_studio_po_no from related sale or purchase order.
        Links the invoice line back to the original Sales Order.
        """
        for line in self:
            if line.x_studio_po_no:
                continue

            # Try from sale line
            if line.sale_line_ids:
                line.x_studio_po_no = line.sale_line_ids[0].order_id.id
                continue

            # Try from purchase line
            if line.purchase_line_id and line.purchase_line_id.order_id.origin:
                sale_order = self.env['sale.order'].search([
                    ('name', '=', line.purchase_line_id.order_id.origin)
                ], limit=1)
                if sale_order:
                    line.x_studio_po_no = sale_order.id
