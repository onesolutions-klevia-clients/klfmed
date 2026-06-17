/** @odoo-module **/
import { patch } from '@web/core/utils/patch';
import { FormController } from '@web/views/form/form_controller';
import { onMounted, onPatched, onWillUnmount } from '@odoo/owl';

const DETAILED_OPERATIONS_DATE_FIELDS = ['expiration_date', 'removal_date'];

// Recursively finds an OWL component by constructor name in the component tree
function findOWLComponent(node, name, depth = 0) {
	if (depth > 20) return null;
	if (node?.component?.constructor?.name === name) return node.component;
	for (const child of Object.values(node?.children || {})) {
		const found = findOWLComponent(child, name, depth + 1);
		if (found) return found;
	}
	return null;
}

// Rewrites date cells in a dialog's list from Odoo locale format to ISO (YYYY-MM-DD).
// Reads date values from the dialog's OWL FormController model — no extra RPC needed.
// @param dialog    - the .o_dialog DOM element
// @param fields    - list of field names to reformat (e.g. ['expiration_date'])
// @param listField - the one2many field name on the form record holding the rows
function reformatDialogDateFields(dialog, { fields = [], listField = 'move_line_ids' } = {}) {
	const root = odoo.__WOWL_DEBUG__?.root;
	const dialogWrapper = findOWLComponent(root?.__owl__ || root, 'DialogWrapper');
	const formCtrl = findOWLComponent(dialogWrapper?.__owl__, 'FormController');
	const records = formCtrl?.model?.root?.data?.[listField]?.records;
	if (!records?.length) return;

	// Rewrites the text node of a DOM element to `after`, in place so OWL keeps its reference.
	const rewriteTextNode = (el, after) => {
		const textNode = [...el.childNodes].find(
			(n) => n.nodeType === Node.TEXT_NODE && n.nodeValue.trim(),
		);
		if (textNode && textNode.nodeValue !== after) {
			console.log(`[klf] "${textNode.nodeValue}" → "${after}"`);
			textNode.nodeValue = after;
		}
	};

	const applyFormatting = () => {
		const rows = [...dialog.querySelectorAll('tbody tr.o_data_row')];
		records.forEach((record, i) => {
			const row = rows[i];
			if (!row) return;
			fields.forEach((field) => {
				const cell = row.querySelector(`td[name="${field}"]`);
				const value = record.data?.[field];
				if (!cell || !value) return;
				try {
					const after = value.toISODate();
					// Read mode: text node directly in the cell
					rewriteTextNode(cell, after);
					// Edit mode after Apply: Odoo renders a button.o_input with the locale date
					const btn = cell.querySelector('button.o_input');
					if (btn) rewriteTextNode(btn, after);
				} catch (e) {
					console.warn(`[klf] ${field}: failed to reformat`, e);
				}
			});
		});
	};

	applyFormatting();

	// Re-apply after each OWL re-render (e.g. when user exits edit mode on a date cell).
	// The `textNode.nodeValue === after` guard in applyFormatting prevents infinite loops.
	const tbody = dialog.querySelector('tbody');
	if (tbody) {
		new MutationObserver(applyFormatting).observe(tbody, {
			childList: true,
			subtree: true,
			characterData: true,
		});
	}
}

patch(FormController.prototype, {
	setup() {
		super.setup(...arguments);

		const isDropship = () =>
			this.props.resModel === 'stock.picking' && this.model.root.data.picking_type_code === 'dropship';

		const injectButton = () => {
			if (!isDropship()) return;
			const container = document.querySelector('.o_statusbar_buttons');
			if (!container || container.querySelector('.klf-reset-qty-btn')) return;

			const btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'btn btn-secondary klf-reset-qty-btn';
			btn.textContent = 'Reset Qty to Ship';
			btn.addEventListener('click', async () => {
				const lines = this.model.root.data.move_ids?.records || [];
				for (const line of lines) {
					await line.update({ quantity: 0 });
				}
			});
			container.appendChild(btn);
		};

		const setupModalObserver = () => {
			const observer = new MutationObserver((mutations) => {
				for (const mutation of mutations) {
					for (const node of mutation.addedNodes) {
						if (node.nodeType !== 1 || !node.classList?.contains('o_dialog')) continue;
						const title = node.querySelector('.modal-title');
						if (title?.textContent?.trim() !== 'Detailed Operations') continue;
						setTimeout(() => {
							reformatDialogDateFields(node, {
								fields: DETAILED_OPERATIONS_DATE_FIELDS,
							});
						}, 0);
					}
				}
			});
			observer.observe(document.body, { childList: true, subtree: true });
			return () => observer.disconnect();
		};

		let disconnectObserver = null;

		onMounted(() => {
			injectButton();
			if (isDropship()) {
				disconnectObserver = setupModalObserver();
			}
		});

		onPatched(injectButton);

		onWillUnmount(() => disconnectObserver?.());
	},
});
