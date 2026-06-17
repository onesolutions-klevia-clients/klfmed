/** @odoo-module **/
import { patch } from '@web/core/utils/patch';
import { FormController } from '@web/views/form/form_controller';
import { onMounted, onPatched, onWillUnmount } from '@odoo/owl';

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);

        const isDropship = () =>
            this.props.resModel === 'stock.picking' &&
            this.model.root.data.picking_type_code === 'dropship';

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
                        if (title?.textContent?.trim() === 'Detailed Operations') {
                            console.log('[klf] Detailed Operations modal opened');
                        }
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
