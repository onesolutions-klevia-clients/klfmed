/** @odoo-module **/

function isOnDropshipPicking() {
    const pickingTypeWidget = document.querySelector('.o_form_view .o_field_widget[name="picking_type_id"]');
    return pickingTypeWidget?.textContent?.trim().toLowerCase().includes('dropship') ?? false;
}

const observer = new MutationObserver((mutations) => {
    if (!isOnDropshipPicking()) return;

    for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
            if (node.nodeType !== 1) continue;
            const dialog = node.classList?.contains('o_dialog') ? node : node.querySelector?.('.o_dialog');
            if (!dialog) continue;
            const title = dialog.querySelector('.modal-title');
            if (title?.textContent?.trim() === 'Detailed Operations') {
                console.log('[klf] Detailed Operations modal opened');
            }
        }
    }
});

observer.observe(document.body, { childList: true, subtree: true });
