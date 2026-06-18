import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class KlfmedInvoiceReport(models.AbstractModel):
    _name = 'report.klf_dropship.report_invoice_klfmed'
    _description = 'KLFMed Commercial Invoice Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        """Populate logistics fields on invoice lines before rendering the report."""
        _logger.info('KLFMed Invoice Report: _get_report_values called for docids=%s', docids)
        docs = self.env['account.move'].browse(docids)

        for move in docs:
            # Re-populate header-level fields from pickings
            move._populate_from_picking()

            # Re-populate line-level fields
            for line in move.invoice_line_ids:
                line._populate_lot_number()
                line._populate_expiration_date()
                line._populate_po_no()
                line._populate_delivery_date()

        # Pre-compute unique PO numbers per invoice for the template
        po_numbers_map = {}
        for move in docs:
            po_numbers = list(dict.fromkeys(
                line.x_studio_po_no_ref
                for line in move.invoice_line_ids
                if line.display_type not in ('line_section', 'line_note') and line.x_studio_po_no_ref
            ))
            po_numbers_map[move.id] = ', '.join(po_numbers)

        # Pre-compute carrier tracking reference(s) from the related dropship picking(s)
        tracking_refs_map = {}
        for move in docs:
            tracking_refs_map[move.id] = ', '.join(self._get_tracking_refs(move))

        return {
            'doc_ids': docids,
            'doc_model': 'account.move',
            'docs': docs,
            'po_numbers_map': po_numbers_map,
            'tracking_refs_map': tracking_refs_map,
        }

    @api.model
    def _get_tracking_refs(self, move):
        """
        Collect unique carrier tracking references from the dropship picking(s)
        linked to an invoice.

        Pickings are traced the same way as in AccountMove._populate_from_picking:
        invoice line → sale/purchase line → stock.move → picking.
        """
        pickings = self.env['stock.picking']
        for line in move.invoice_line_ids:
            for sale_line in line.sale_line_ids:
                pickings |= sale_line.move_ids.mapped('picking_id')
            if line.purchase_line_id:
                pickings |= line.purchase_line_id.move_ids.mapped('picking_id')

        return list(dict.fromkeys(
            picking.carrier_tracking_ref
            for picking in pickings
            if picking.carrier_tracking_ref
        ))
