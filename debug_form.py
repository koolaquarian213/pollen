"""Debug: dump all select options from a Greenhouse form."""
import asyncio

async def main():
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()

    url = "https://boards.greenhouse.io/embed/job_app?token=7307062"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # Check for iframe
    iframe = await page.query_selector('iframe[src*="greenhouse"], iframe[id*="grnhse"]')
    if iframe:
        frame = await iframe.content_frame()
        if frame:
            page = frame
            await asyncio.sleep(1)

    selects = await page.query_selector_all('select')
    print(f"\nFound {len(selects)} dropdowns:\n")

    for i, sel in enumerate(selects):
        info = await sel.evaluate("""el => {
            const id = el.id || '';
            const name = el.name || '';
            const required = el.required;
            let label = '';
            if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent.trim(); }
            if (!label) { const p = el.closest('.field,.form-group,fieldset,div'); if (p) { const l = p.querySelector('label,legend'); if (l) label = l.textContent.trim(); }}
            const options = Array.from(el.options).map(o => ({ text: o.text.trim(), value: o.value, index: o.index, selected: o.selected }));
            return { id, name, label: label.substring(0, 80), required, options, currentValue: el.value };
        }""")

        print(f"--- Select #{i+1} ---")
        print(f"  Label: {info['label']}")
        print(f"  ID: {info['id']}")
        print(f"  Name: {info['name']}")
        print(f"  Required: {info['required']}")
        print(f"  Current value: '{info['currentValue']}'")
        print(f"  Options:")
        for opt in info['options']:
            marker = " <<<" if opt['selected'] else ""
            print(f"    [{opt['index']}] value='{opt['value']}' text='{opt['text']}'{marker}")
        print()

    # Also dump unchecked checkboxes
    checkboxes = await page.query_selector_all('input[type="checkbox"]')
    if checkboxes:
        print(f"\nFound {len(checkboxes)} checkboxes:\n")
        for cb in checkboxes:
            info = await cb.evaluate("""el => {
                let label = '';
                const id = el.id;
                if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent.trim(); }
                if (!label) { const p = el.closest('.field,.form-group,div,label'); if (p) label = p.textContent.trim(); }
                return { id: el.id, name: el.name, checked: el.checked, required: el.required, label: label.substring(0, 80) };
            }""")
            status = "✓" if info['checked'] else "✗"
            print(f"  {status} [{info['name']}] {info['label'][:60]}  required={info['required']}")

    await browser.close()
    await pw.stop()

asyncio.run(main())
