import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Constant factory location used for EXW/FCA incoterms (goods handed over at the
# factory, not at a port). Customer-confirmed: always WENZHOU (CHINA).
INCOTERM_FACTORY_LOCATION = 'WENZHOU (CHINA)'

# Incoterm code → which location qualifies the term on the commercial invoice.
# Customer-confirmed Incoterms 2020 mapping:
#   EXW / FCA                          → factory location (constant above)
#   FOB / FAS                          → port of loading
#   CFR / CIF / CPT / CIP / DAP / DPU / DDP → port of destination
#   any other code                     → port of loading (fallback, prior behavior)
INCOTERM_FACTORY_CODES = {'EXW', 'FCA'}
INCOTERM_LOADING_CODES = {'FOB', 'FAS'}
INCOTERM_DESTINATION_CODES = {'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'}


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

        # Pre-compute the Incoterms line ("<CODE> <LOCATION>") per invoice
        incoterms_map = {}
        for move in docs:
            incoterms_map[move.id] = self._get_incoterm_display(move)

        return {
            'doc_ids': docids,
            'doc_model': 'account.move',
            'docs': docs,
            'po_numbers_map': po_numbers_map,
            'tracking_refs_map': tracking_refs_map,
            'incoterms_map': incoterms_map,
        }

    @api.model
    def _get_incoterm_display(self, move):
        """
        Build the Incoterms line for the commercial invoice as "<CODE> <LOCATION>".

        The qualifying location depends on the incoterm code (see mapping
        constants): factory location for EXW/FCA, port of loading for FOB/FAS,
        port of destination for CFR/CIF/CPT/CIP/DAP/DPU/DDP, and port of loading
        as the fallback for any other code.

        Only the incoterm *code* is shown, never its name. Returns an empty
        string when no incoterm is set so the template hides the line entirely.
        """
        incoterm = move.invoice_incoterm_id
        if not incoterm:
            return ''
        code = (incoterm.code or '').upper()
        if code in INCOTERM_FACTORY_CODES:
            location = INCOTERM_FACTORY_LOCATION
        elif code in INCOTERM_DESTINATION_CODES:
            location = move.x_studio_port_of_destination or ''
        else:
            # FOB/FAS and any other (fallback) incoterm → port of loading
            location = move.x_studio_port_of_loading or ''
        return ' '.join(filter(None, [code, location.strip().upper()]))

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

        # Strip and skip blank/whitespace-only references so the resulting map
        # value is truly empty when there is nothing real to show. Otherwise a
        # whitespace-only carrier_tracking_ref would pass the template's t-if
        # guard and render an empty "Tracking Ref. :" label.
        return list(dict.fromkeys(
            picking.carrier_tracking_ref.strip()
            for picking in pickings
            if picking.carrier_tracking_ref and picking.carrier_tracking_ref.strip()
        ))
