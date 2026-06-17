/** @odoo-module **/
import { patch } from '@web/core/utils/patch';
import { FormController } from '@web/views/form/form_controller';
import { onMounted, onPatched } from '@odoo/owl';

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);

        const injectButton = () => {
            const container = document.querySelector('.o_statusbar_buttons');
            if (!container) return;

            if (this.props.resModel !== 'stock.picking' || this.model.root.data.picking_type_code !== 'dropship') {
                container.querySelector('.klf-reset-qty-btn')?.remove();
                return;
            }
            if (container.querySelector('.klf-reset-qty-btn')) return;

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

        onMounted(injectButton);
        onPatched(injectButton);
    },
});
