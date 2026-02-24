import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class StockMove(models.Model):
    _inherit = 'stock.move'

    @api.model_create_multi
    def create(self, vals_list):
        """
        Auto-populate x_studio_po_no from the related purchase order.
        Links the stock move back to the original Sales Order.
        """
        moves = super().create(vals_list)
        for move in moves:
            _logger.warning("KLF_DROPSHIP: StockMove created: %s (id=%s) product=%s",
                         move.reference or move.id, move.id, move.product_id.name if move.product_id else 'N/A')
            # Try to get the sale order from the purchase order origin
            if move.purchase_line_id and move.purchase_line_id.order_id:
                po = move.purchase_line_id.order_id
                _logger.warning("KLF_DROPSHIP: StockMove has PO: %s (origin: %s)", po.name, po.origin)
                if po.origin:
                    sale_order = self.env['sale.order'].search([
                        ('name', '=', po.origin)
                    ], limit=1)
                    if sale_order:
                        po_number = sale_order.x_studio_purchase_order_number
                        _logger.warning("KLF_DROPSHIP: Setting x_studio_po_no = %s on StockMove %s",
                                     po_number, move.id)
                        if po_number:
                            move.x_studio_po_no = po_number
                # Propagate delivery date from PO line to stock move
                if move.purchase_line_id and move.purchase_line_id.x_studio_delivery_date:
                    move.x_studio_delivery_date = move.purchase_line_id.x_studio_delivery_date
                    _logger.warning("KLF_DROPSHIP: Propagated delivery_date %s to StockMove %s",
                                 move.purchase_line_id.x_studio_delivery_date, move.id)
        return moves

