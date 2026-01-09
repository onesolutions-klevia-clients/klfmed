import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.model_create_multi
    def create(self, vals_list):
        """
        Auto-populate x_studio_supplier_po on the related Sales Order.
        When a PO is created from a SO (dropship), link it back to the SO.
        """
        orders = super().create(vals_list)
        for order in orders:
            _logger.warning("KLF_DROPSHIP: PO created: %s (origin: %s)", order.name, order.origin)
            if order.origin:
                # Find the sale order matching the origin
                sale_order = self.env['sale.order'].search([
                    ('name', '=', order.origin)
                ], limit=1)
                _logger.warning("KLF_DROPSHIP: Found SO: %s", sale_order.name if sale_order else 'None')
                if sale_order:
                    sale_order.x_studio_supplier_po = order.id
                    _logger.warning("KLF_DROPSHIP: Set x_studio_supplier_po = %s on SO %s", order.id, sale_order.name)
                    # Also update PO lines with the SO reference
                    _logger.warning("KLF_DROPSHIP: PO has %d lines", len(order.order_line))
                    for line in order.order_line:
                        _logger.warning("KLF_DROPSHIP: Line: %s (id=%s) qty=%s -> x_studio_po_no=%s",
                                     line.product_id.name, line.id, line.product_qty, line.x_studio_po_no)
                        if not line.x_studio_po_no:
                            line.x_studio_po_no = sale_order.id
                            _logger.warning("KLF_DROPSHIP: Updated line %s x_studio_po_no = %s", line.id, sale_order.id)
        return orders


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        """
        Auto-populate x_studio_po_no from the purchase order origin field.
        Links the PO line back to the original Sales Order.
        """
        lines = super().create(vals_list)
        for line in lines:
            _logger.warning("KLF_DROPSHIP: POLine created: %s (id=%s) product=%s qty=%s",
                         line.order_id.name if line.order_id else 'N/A', line.id,
                         line.product_id.name, line.product_qty)
            if line.order_id and line.order_id.origin:
                _logger.warning("KLF_DROPSHIP: POLine has origin: %s", line.order_id.origin)
                # Find the sale order matching the origin
                sale_order = self.env['sale.order'].search([
                    ('name', '=', line.order_id.origin)
                ], limit=1)
                if sale_order:
                    _logger.warning("KLF_DROPSHIP: Setting x_studio_po_no = %s (SO: %s) on line %s",
                                 sale_order.id, sale_order.name, line.id)
                    line.x_studio_po_no = sale_order.id
        return lines

