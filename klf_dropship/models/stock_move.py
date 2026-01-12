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
                        _logger.warning("KLF_DROPSHIP: Setting x_studio_po_no = %s on StockMove %s",
                                     sale_order.id, move.id)
                        move.x_studio_po_no = sale_order.id
        return moves

