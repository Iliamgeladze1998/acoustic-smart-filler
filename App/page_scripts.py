"""Selenium page scripts: scan CS-Cart product form context and apply AI fill data."""

from __future__ import annotations

import json
import re
import time
from typing import Any

try:
    from scrape_cache import (
        categories_are_usable as _cache_categories_usable,
        load_categories as _disk_load_categories,
        load_feature_option_map as _disk_load_features,
        merge_and_save_feature_options as _disk_save_features,
        save_categories as _disk_save_categories,
        shop_key as _cache_shop_key,
    )
except ImportError:  # pragma: no cover
    def _cache_categories_usable(items):  # type: ignore
        return False

    def _disk_load_categories(admin_base_url):  # type: ignore
        return None

    def _disk_load_features(admin_base_url):  # type: ignore
        return {}

    def _disk_save_features(admin_base_url, discovered):  # type: ignore
        return None

    def _disk_save_categories(admin_base_url, items):  # type: ignore
        return None

    def _cache_shop_key(admin_base_url):  # type: ignore
        return "default"

# Open Features / Specifications / Video tabs so AJAX content is present.
OPEN_TABS_SCRIPT = r"""
function textOf(el) {
    return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
}

function isSafeInPageTab(el) {
    if (!el) return false;
    // Never touch top admin menu / left nav — those leave the product edit page
    if (el.closest(
        '#header_navbar, #header_subnav, .navbar, .mainbox-nav, .sidebar, ' +
        '#actions_panel, .top-menu, .admin-content-menu, .nav-pills.nav-stacked, ' +
        '#main_column_left, .mainbar, .menu-container, #menu'
    )) return false;

    const href = (el.getAttribute('href') || '').trim();
    const lower = href.toLowerCase();

    // Block any full navigation (especially product_features.manage)
    if (lower.includes('product_features')) return false;
    if (lower.includes('dispatch=') && !lower.startsWith('#')) return false;
    if ((lower.startsWith('http://') || lower.startsWith('https://') || lower.includes('.php')) && !lower.startsWith('#')) {
        return false;
    }

    const pureHash = href.startsWith('#') && href.length > 1;
    const targetId = el.getAttribute('data-ca-target-id') || '';
    const looksLikeTab = pureHash || targetId || el.classList.contains('cm-js') ||
        (el.parentElement && el.parentElement.classList.contains('cm-js'));
    if (!looksLikeTab) return false;

    const inProductUi = el.closest(
        'form[name="product_update_form"], #product_update_form, form#product_update_form, ' +
        '.cm-product-form, .tabs, .cm-j-tabs, .cm-tabs, .nav-tabs, .ty-tabs, ' +
        '#tabs_content, .cm-tabs-content, [id*="product_update"], #content_management'
    );

    if (pureHash) {
        const id = href.slice(1).toLowerCase();
        if (id.startsWith('content_') || id.includes('feature') || id.includes('video') ||
            id.includes('categor') || id.includes('seo') || id.includes('detailed') ||
            id.includes('ab__') || id.includes('addon')) {
            return true;
        }
        return !!inProductUi;
    }
    return !!inProductUi;
}

function scoreTab(text) {
    const t = (text || '').toLowerCase();
    let score = 0;
    const pairs = [
        ['feature', 5], ['spec', 5], ['მახასიათ', 6], ['თვისებ', 6], ['სპეციფ', 5],
        ['video', 5], ['ვიდეო', 6], ['gallery', 3], ['გალერ', 4],
        ['categor', 5], ['კატეგ', 6], ['კატეგორ', 6],
        ['ab:', 4], ['seo', 2]
    ];
    for (const [k, s] of pairs) {
        if (t.includes(k)) score += s;
    }
    return score;
}

const root =
    document.querySelector('form[name="product_update_form"]') ||
    document.querySelector('#product_update_form') ||
    document.querySelector('#content_management') ||
    document.body;

const scoped = root.querySelectorAll(
    '.cm-j-tabs a, .cm-tabs a, .nav-tabs a, .tabs a, ul.nav a, ' +
    'a[href^="#content_"], a[data-ca-target-id], li.cm-js > a, a.cm-js'
);

const candidates = Array.from(scoped).filter(isSafeInPageTab);
document.querySelectorAll('a[href^="#content_"]').forEach(el => {
    if (isSafeInPageTab(el) && !candidates.includes(el)) candidates.push(el);
});

const ranked = candidates
    .map(el => ({ el, text: textOf(el), score: scoreTab(textOf(el)) }))
    .filter(x => x.score >= 3)
    .sort((a, b) => b.score - a.score);

const clicked = [];
const seen = new Set();
for (const item of ranked) {
    const key = item.text.slice(0, 80) + '|' + (item.el.getAttribute('href') || '');
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const href = (item.el.getAttribute('href') || '');
    if (href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript:')) continue;
    try {
        item.el.click();
        clicked.push(item.text);
    } catch (e) {}
    if (clicked.length >= 8) break;
}

[
    'content_features', 'content_product_features', 'content_categories', 'content_category',
    'content_ab__video_gallery',
    'content_ab_video_gallery', 'content_video_gallery', 'content_addons',
    'content_seo', 'content_detailed'
].forEach(id => {
    const n = document.getElementById(id);
    if (n) {
        n.classList.remove('hidden', 'collapsed');
        n.style.display = '';
    }
});

return {
    clicked,
    tabCount: ranked.length,
    stillOnProduct: location.href.includes('dispatch=products.update'),
    href: location.href
};
"""

# Scans current product edit tab for field values and feature option lists.
SCAN_SCRIPT = r"""
function val(selectors) {
    for (const s of selectors) {
        const el = document.querySelector(s);
        if (el && 'value' in el) return el.value;
    }
    return '';
}

function textContent(el) {
    return (el && (el.innerText || el.textContent) || '').trim();
}

// ---------- Rich feature / category scan (dropdowns, multi, checkboxes, radios) ----------
function cssEsc(s) {
    try {
        if (window.CSS && CSS.escape) return CSS.escape(String(s));
    } catch (e) {}
    return String(s).replace(/([^a-zA-Z0-9_-])/g, '\\$1');
}
function cleanLabel(t) {
    t = (t || '').replace(/\s+/g, ' ').replace(/[:*：]+$/g, '').trim();
    // de-dupe doubled labels like "ავტორი ავტორი"
    const parts = t.split(/\s+/).filter(Boolean);
    if (parts.length >= 2 && parts.length % 2 === 0) {
        const half = parts.length / 2;
        const a = parts.slice(0, half).join(' ');
        const b = parts.slice(half).join(' ');
        if (a === b) return a;
    }
    const m = t.match(/^(.+?)\s+\1$/);
    if (m) return m[1].trim();
    return t;
}

function optionDisplayLabel(o) {
    if (!o) return '';
    // Prefer labels we injected during open-dropdown enrichment
    const candidates = [
        o.getAttribute('data-asf-label'),
        o.getAttribute('data-ca-name'),
        o.getAttribute('data-name'),
        o.getAttribute('title'),
        o.textContent,
        o.innerText,
        o.label
    ];
    let best = '';
    for (const c of candidates) {
        const lab = cleanLabel(c || '');
        if (!lab) continue;
        // Prefer real names over raw numeric IDs
        if (!best) best = lab;
        if (!/^\d+$/.test(lab) && lab.length > 0) return lab;
    }
    return best;
}

function controlGroupLabel(el) {
    if (!el) return '';
    if (el.id) {
        const lab = document.querySelector('label[for="' + cssEsc(el.id) + '"]');
        if (lab) return cleanLabel(textContent(lab));
    }
    const group = el.closest(
        '.control-group, .ty-control-group, tr, .feature-item, li, .cm-feature, ' +
        '.features-list, [id*="feature"], .cm-field-container, fieldset, .form-group'
    );
    if (group) {
        const lab = group.querySelector(
            ':scope > label, :scope > .control-label, label.control-label, .control-label, ' +
            'th, .feature-name, .span3, td:first-child, legend'
        );
        if (lab && !lab.contains(el)) {
            const t = cleanLabel(textContent(lab));
            if (t && t.length < 120) return t;
        }
    }
    let p = el.previousElementSibling;
    for (let i = 0; i < 4 && p; i++, p = p.previousElementSibling) {
        if (p.tagName === 'LABEL') return cleanLabel(textContent(p));
    }
    return '';
}

function parseFeatureId(name) {
    // product_data[product_features][12][variant_id]  OR  product_features[12]
    const m = (name || '').match(/product_features\]?\[(\d+)\]|features\[(\d+)\]|feature_id[:=]?(\d+)/i);
    if (m) return m[1] || m[2] || m[3] || '';
    const m2 = (name || '').match(/\[(\d{1,6})\]/);
    return m2 ? m2[1] : '';
}

function isFeatureish(el) {
    if (!el) return false;
    const n = (el.getAttribute('name') || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    const t = (el.type || '').toLowerCase();
    if (t === 'hidden' || t === 'file' || t === 'submit' || t === 'button' || t === 'image') return false;
    if (n.includes('category') || id.includes('category')) return false;
    if (n.includes('product_features') || n.includes('feature_data') || n.includes('[variants]')) return true;
    if (id.includes('feature') || n.includes('feature')) return true;
    if (el.closest('#content_features, #content_product_features, [id*="content_feature"], .features, .cm-features')) {
        if (n.includes('price') || n.includes('product_data[product]')) return false;
        return true;
    }
    return false;
}

function optionFrom(el) {
    return {
        value: String(el.value || ''),
        label: cleanLabel(el.getAttribute('data-ca-label') || el.getAttribute('title') || textContent(el) || el.value || '')
    };
}

const featuresMap = {}; // key = field_name or group key

function upsertFeature(entry) {
    const key = entry.group_key || entry.field_name;
    if (!key) return;
    if (!featuresMap[key]) {
        featuresMap[key] = entry;
        return;
    }
    // merge options
    const existing = featuresMap[key];
    const seen = new Set(existing.options.map(o => o.value + '|' + o.label));
    (entry.options || []).forEach(o => {
        const k = o.value + '|' + o.label;
        if (!seen.has(k)) {
            existing.options.push(o);
            seen.add(k);
        }
    });
    if (entry.current_values && entry.current_values.length) {
        entry.current_values.forEach(v => {
            if (!existing.current_values.includes(v)) existing.current_values.push(v);
        });
    }
    if (entry.selected_labels) {
        entry.selected_labels.forEach(v => {
            if (!existing.selected_labels.includes(v)) existing.selected_labels.push(v);
        });
    }
    // Upgrade mode if any member is multi
    if (entry.selection_mode === 'multi' || entry.selection_mode === 'checkbox_group') {
        existing.selection_mode = entry.selection_mode;
        existing.multiple = true;
    }
}

// SELECT controls
document.querySelectorAll('select').forEach(el => {
    if (!isFeatureish(el)) return;
    const name = el.getAttribute('name') || '';
    const options = Array.from(el.options || []).map(o => {
        const value = String(o.value);
        let label = optionDisplayLabel(o);
        // Prefer visible select2 choice text for selected option
        if ((!label || label === value) && o.selected) {
            const cont = el.closest('.control-group, .ty-control-group, .cm-field-container, div') || el.parentElement;
            if (cont) {
                const rendered = cont.querySelector(
                    '.select2-selection__rendered, .select2-chosen, .select2-selection__choice'
                );
                if (rendered) {
                    const rt = cleanLabel((rendered.getAttribute('title') || textContent(rendered) || '')
                        .replace(/×/g, '').trim());
                    if (rt && rt !== '×' && !/^[\d\s]+$/.test(rt)) label = rt;
                }
            }
        }
        if (!label) label = value;
        return { value: value, label: label, selected: !!o.selected };
    });
    const selected = options.filter(o => o.selected);
    const multiple = !!el.multiple;
    upsertFeature({
        id: parseFeatureId(name),
        name: controlGroupLabel(el) || name,
        label: controlGroupLabel(el) || name,
        field_name: name,
        group_key: name,
        tag: 'select',
        type: multiple ? 'select_multi' : 'select_single',
        selection_mode: multiple ? 'multi' : 'single',
        multiple: multiple,
        options: options,
        current: selected.length ? selected[0].value : (el.value || ''),
        current_values: selected.map(o => o.value),
        selected_labels: selected.map(o => o.label),
        allow_empty: true
    });
});

// TEXT / TEXTAREA free-value features
document.querySelectorAll(
    'input[type="text"], input[type="number"], input:not([type]), textarea'
).forEach(el => {
    if (!isFeatureish(el)) return;
    const name = el.getAttribute('name') || '';
    if (!name) return;
    if (name.includes('variant_id') && el.tagName === 'INPUT') return;
    upsertFeature({
        id: parseFeatureId(name),
        name: controlGroupLabel(el) || name,
        label: controlGroupLabel(el) || name,
        field_name: name,
        group_key: name,
        tag: el.tagName.toLowerCase(),
        type: 'text',
        selection_mode: 'text',
        multiple: false,
        options: [],
        current: el.value || '',
        current_values: el.value ? [el.value] : [],
        selected_labels: el.value ? [el.value] : [],
        allow_empty: true
    });
});

// RADIO groups
const radioGroups = {};
document.querySelectorAll('input[type="radio"]').forEach(el => {
    if (!isFeatureish(el)) return;
    const name = el.getAttribute('name') || '';
    if (!name) return;
    if (!radioGroups[name]) radioGroups[name] = [];
    radioGroups[name].push(el);
});
Object.entries(radioGroups).forEach(([name, els]) => {
    const first = els[0];
    const options = els.map(el => {
        let lab = '';
        if (el.id) {
            const l = document.querySelector('label[for="' + cssEsc(el.id) + '"]');
            if (l) lab = cleanLabel(textContent(l));
        }
        if (!lab) {
            const near = el.closest('label');
            if (near) lab = cleanLabel(textContent(near));
        }
        if (!lab) lab = String(el.value || '');
        return { value: String(el.value || ''), label: lab, selected: !!el.checked };
    });
    const selected = options.filter(o => o.selected);
    upsertFeature({
        id: parseFeatureId(name),
        name: controlGroupLabel(first) || name,
        label: controlGroupLabel(first) || name,
        field_name: name,
        group_key: name,
        tag: 'radio',
        type: 'radio',
        selection_mode: 'single',
        multiple: false,
        options: options,
        current: selected.length ? selected[0].value : '',
        current_values: selected.map(o => o.value),
        selected_labels: selected.map(o => o.label),
        allow_empty: true
    });
});

// CHECKBOX features (often multi-variant)
const checkGroups = {};
document.querySelectorAll('input[type="checkbox"]').forEach(el => {
    if (!isFeatureish(el)) return;
    const name = el.getAttribute('name') || '';
    if (!name) return;
    // group by name without trailing indices sometimes shared
    const groupKey = name.replace(/\[\d+\]$/, '[]');
    if (!checkGroups[groupKey]) checkGroups[groupKey] = [];
    checkGroups[groupKey].push(el);
});
Object.entries(checkGroups).forEach(([gkey, els]) => {
    const first = els[0];
    const name = first.getAttribute('name') || gkey;
    // Single lone checkbox with no siblings of same feature often still multi-capable feature flag
    const options = els.map(el => {
        let lab = '';
        if (el.id) {
            const l = document.querySelector('label[for="' + cssEsc(el.id) + '"]');
            if (l) lab = cleanLabel(textContent(l));
        }
        if (!lab) {
            const near = el.closest('label');
            if (near) lab = cleanLabel(textContent(near).replace(/\s+/g, ' '));
        }
        if (!lab) lab = String(el.value || 'on');
        return {
            value: String(el.value || 'Y'),
            label: lab,
            selected: !!el.checked,
            field_name: el.getAttribute('name') || name
        };
    });
    const selected = options.filter(o => o.selected);
    const multi = els.length > 1 || name.includes('[]') || name.includes('variants');
    upsertFeature({
        id: parseFeatureId(name),
        name: controlGroupLabel(first) || name,
        label: controlGroupLabel(first) || name,
        field_name: name,
        group_key: gkey,
        tag: 'checkbox',
        type: multi ? 'checkbox_group' : 'checkbox',
        selection_mode: multi ? 'multi' : 'single',
        multiple: multi,
        options: options,
        current: selected.length ? selected[0].value : '',
        current_values: selected.map(o => o.value),
        selected_labels: selected.map(o => o.label),
        allow_empty: true
    });
});

// Merge options from open-dropdown / AJAX cache (window.__ASF_FEATURE_OPTIONS)
const cachedOpts = window.__ASF_FEATURE_OPTIONS || {};
Object.keys(featuresMap).forEach(key => {
    const f = featuresMap[key];
    const cache = cachedOpts[f.field_name] || cachedOpts[key] || cachedOpts[f.id] || null;
    if (!cache || !cache.length) return;
    const byVal = {};
    (f.options || []).forEach(o => { byVal[String(o.value)] = o; });
    cache.forEach(it => {
        const v = String(it.value == null ? '' : it.value);
        const lab = cleanLabel(it.label || '');
        if (!lab && v === '') return;
        if (!byVal[v]) {
            byVal[v] = { value: v, label: lab || v, selected: false };
        } else {
            const cur = byVal[v];
            const curLab = (cur.label || '').trim();
            // Upgrade numeric/empty labels to real names
            if (lab && !/^\d+$/.test(lab) && ( !curLab || curLab === v || /^\d+$/.test(curLab) )) {
                cur.label = lab;
            }
        }
    });
    f.options = Object.values(byVal);
    // Refresh selected labels from upgraded options
    f.selected_labels = (f.options || []).filter(o => o.selected).map(o => o.label);
});

const features = Object.values(featuresMap).map(f => {
    // Prefer human labels over raw field names
    f.name = cleanLabel(f.label || f.name);
    f.label = cleanLabel(f.label || f.name);
    // Drop empty placeholder options that are truly blank unless selected
    f.options = (f.options || []).filter(o => {
        if (o.selected) return true;
        const lab = (o.label || '').trim();
        if (!lab && !o.value) return false;
        return true;
    });
    // Deduplicate options by value, keeping non-numeric label
    const byVal = {};
    f.options.forEach(o => {
        const v = String(o.value);
        if (!byVal[v]) { byVal[v] = o; return; }
        const a = byVal[v];
        if (/^\d+$/.test(a.label || '') && !/^\d+$/.test(o.label || '')) byVal[v] = o;
        else if (o.selected) byVal[v] = Object.assign({}, a, { selected: true, label: a.label || o.label });
    });
    f.options = Object.values(byVal);
    return f;
}).sort((a, b) => (a.label || '').localeCompare(b.label || '', 'ka'));

// ---------- Categories (product form only — not admin menus / storefronts) ----------
const categoryOptions = [];
const catSeen = new Set();

function isProductCategorySelect(el) {
    if (!el) return false;
    const n = (el.getAttribute('name') || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    // Must be product category_ids — never company/storefront navigation
    if (n.includes('product_data') && n.includes('category')) return true;
    if (n.includes('category_ids') && !n.includes('company') && !n.includes('storefront')) return true;
    if (id.includes('product_categor')) return true;
    // Label-bound product field
    const group = el.closest('.control-group, .ty-control-group, .form-group, tr');
    if (group) {
        const lab = group.querySelector('label, .control-label');
        const t = ((lab && (lab.innerText || lab.textContent)) || '').toLowerCase();
        if ((t.includes('კატეგორ') || t.includes('categor')) &&
            !t.includes('storefront') && !t.includes('company')) {
            return n.includes('category') || el.classList.contains('cm-object-picker') ||
                el.classList.contains('select2-hidden-accessible');
        }
    }
    return false;
}

function pushCategory(opt) {
    const value = String(opt.value || '');
    const label = cleanLabel(opt.label || '');
    if (!label && !value) return;
    // Reject admin/marketplace noise from wrong pickers
    const low = label.toLowerCase();
    if (/(alexbranding|cart-power|cs-cart|add-on|addon market|storefront|გადახდის|ტრანსპორტირ)/i.test(label)) {
        return;
    }
    if (!value && !/[\u10A0-\u10FF]/.test(label) && label.length < 3) return;
    const key = (opt.field_name || '') + '|' + value + '|' + label;
    if (catSeen.has(key)) return;
    catSeen.add(key);
    categoryOptions.push({
        id: value,
        value: value,
        label: label || value,
        field_name: opt.field_name || '',
        selected: !!opt.selected,
        path: opt.path || label || value
    });
}

// Only product category selects (object-picker) — never global admin trees
document.querySelectorAll('select').forEach(el => {
    if (!isProductCategorySelect(el)) return;
    Array.from(el.options || []).forEach(o => {
        if (!String(o.value || '').trim() && !cleanLabel(o.textContent)) return;
        pushCategory({
            value: o.value,
            label: cleanLabel(o.textContent || o.value),
            field_name: el.getAttribute('name') || '',
            selected: !!o.selected
        });
    });
});

// Selected chips only inside product category control group
document.querySelectorAll('label, .control-label').forEach(lab => {
    const t = ((lab.innerText || lab.textContent) || '').toLowerCase();
    if (!(t.includes('კატეგორ') || t.includes('categor'))) return;
    if (t.includes('storefront') || t.includes('feature')) return;
    const g = lab.closest('.control-group, .ty-control-group, .form-group, tr') || lab.parentElement;
    if (!g) return;
    g.querySelectorAll(
        '.select2-selection__choice, .select2-selection__rendered, ' +
        '.object-picker__selection-text, .cm-object-picker-selected'
    ).forEach(ch => {
        const lab2 = cleanLabel((ch.getAttribute('title') || ch.innerText || '').replace(/×/g, ''));
        if (lab2 && lab2.length > 1 && !/^\d+$/.test(lab2)) {
            pushCategory({ value: lab2, label: lab2, field_name: '', selected: true });
        }
    });
});

const categories = categoryOptions.filter(c => c.selected).map(c => c.label);
const available_category_options = categoryOptions;

const videoFields = [];
document.querySelectorAll('input, textarea, select').forEach((el, idx) => {
    const n = (el.getAttribute('name') || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    const blob = n + ' ' + id;
    if (
        blob.includes('video') || blob.includes('ab__vg') || blob.includes('ab_vg') ||
        blob.includes('ab__video') || blob.includes('youtube') || blob.includes('vimeo') ||
        blob.includes('video_path') || blob.includes('video_url')
    ) {
        if (videoFields.length < 40) {
            videoFields.push({
                name: el.getAttribute('name') || '',
                id: el.id || '',
                type: el.type || el.tagName.toLowerCase(),
                value: (el.value || '').slice(0, 120),
                tag: el.tagName.toLowerCase()
            });
        }
    }
});

const videoHints = {
    present: videoFields.length > 0 ||
        !!(document.body.innerHTML.toLowerCase().includes('ab__video') ||
           document.body.innerHTML.toLowerCase().includes('video gallery') ||
           document.body.innerHTML.toLowerCase().includes('ვიდეო')),
    fields_sample: videoFields,
    add_buttons: Array.from(document.querySelectorAll('a, button')).filter(el => {
        const t = textContent(el).toLowerCase();
        return t.includes('add video') || t.includes('добавить') || t.includes('ვიდეო') && t.includes('დამატ') ||
            (t.includes('add') && t.includes('video')) || t.includes('+');
    }).slice(0, 8).map(el => textContent(el))
};

const tabs = Array.from(document.querySelectorAll('.cm-js, .nav-tabs a, [id^="products_"] a, .tabs a, a[href^="#content_"]'))
    .map(a => textContent(a))
    .filter(Boolean)
    .slice(0, 50);

return {
    url: location.href,
    product_name: val([
        '#product_description_product',
        'input[name="product_data[product]"]'
    ]),
    price: val([
        '#elm_price_price',
        'input[name="product_data[price]"]'
    ]),
    old_price: val([
        '#elm_price_list_price',
        'input[name="product_data[list_price]"]',
        'input[name="product_data[old_price]"]'
    ]),
    existing_tags: (function() {
        const tagEls = document.querySelectorAll(
            'input[name*="tags"], input[name*="product_tags"], ' +
            '.tags input, .cm-tags input, select[name*="tags"]'
        );
        for (const el of tagEls) {
            if (el.value && el.value.trim()) return el.value.trim();
        }
        const s2tags = document.querySelectorAll(
            '.select2-selection--tags .select2-selection__choice'
        );
        if (s2tags.length) {
            return Array.from(s2tags).map(e => e.textContent.trim()).join(', ');
        }
        return '';
    })(),
    product_code: val([
        '#elm_product_code',
        'input[name="product_data[product_code]"]'
    ]),
    full_description: val([
        '#elm_full_descr',
        '#elm_product_full_descr',
        'textarea[name="product_data[full_description]"]'
    ]),
    promo_text: val([
        '#elm_product_promo_text',
        'textarea[name="product_data[promo_text]"]'
    ]),
    page_title: val([
        '#elm_product_page_title',
        'input[name="product_data[page_title]"]'
    ]),
    meta_description: val([
        '#elm_product_meta_descr',
        'textarea[name="product_data[meta_description]"]'
    ]),
    meta_keywords: val([
        '#elm_product_meta_keywords',
        'textarea[name="product_data[meta_keywords]"]'
    ]),
    seo_name: val([
        '#elm_seo_name',
        'input[name="product_data[seo_name]"]',
        'input[name="seo_name"]',
        'input[name*="[seo_name]"]'
    ]),
    available_features: features,
    available_categories: categories,
    available_category_options: available_category_options,
    category_selection_mode: 'multi',
    video_gallery: videoHints,
    visible_tabs: tabs,
    logged_in_user: (function () {
        function clean(s) {
            return String(s || '')
                .replace(/[\u00a0\s]+/g, ' ')
                .replace(/^(hello|hi|გამარჯობა|logged\s*in\s*as|account)[:\s]*/i, '')
                .replace(/\s*[•|·].*$/, '')
                .trim();
        }
        function looksLikeName(s) {
            s = clean(s);
            if (!s || s.length < 2 || s.length > 80) return false;
            if (/@(?:gmail|mail|yahoo|hotmail|acoustic)/i.test(s)) return true;
            if (/password|login|sign\s*out|log\s*out|меню|menu|dashboard/i.test(s)) return false;
            // Prefer human names (letters, spaces, hyphens). Allow Georgian.
            if (!/[A-Za-z\u10A0-\u10FF]{2,}/.test(s)) return false;
            return true;
        }
        const out = { name: '', first: '', last: '', email: '', user_id: '', source: '' };

        // CS-Cart / Tygh runtime (varies by version)
        try {
            const T = window.Tygh || {};
            const cand = T.user || T.user_info || (T.runtime && T.runtime.user) || null;
            if (cand && typeof cand === 'object') {
                const fn = clean(cand.firstname || cand.first_name || cand.fname || '');
                const ln = clean(cand.lastname || cand.last_name || cand.lname || '');
                const full = clean(cand.name || cand.user_name || cand.username ||
                    [fn, ln].filter(Boolean).join(' '));
                const em = clean(cand.email || '');
                if (full && looksLikeName(full)) {
                    out.name = full; out.first = fn; out.last = ln; out.email = em;
                    out.user_id = String(cand.user_id || cand.id || '');
                    out.source = 'tygh';
                    return out;
                }
                if (em) { out.email = em; out.name = em; out.source = 'tygh_email'; return out; }
            }
        } catch (e) {}

        const selectors = [
            'a[href*="profiles.update"]',
            'a[href*="dispatch=profiles"]',
            '.nav__profile', '.nav-profile', '.nav__user',
            '.dropdown-top-menu-item-profile',
            '.top-bar .dropdown-toggle',
            '#header_navbar .dropdown-toggle',
            '#header_subnav .dropdown-toggle',
            '.navbar-right .dropdown-toggle',
            '.ty-account-info__title',
            '.cm-account-info',
            '[data-ca-profile-name]',
            '.admin-content-menu .dropdown-toggle'
        ];
        for (const sel of selectors) {
            let nodes = [];
            try { nodes = Array.from(document.querySelectorAll(sel)); } catch (e) { nodes = []; }
            for (const el of nodes) {
                let t = clean(el.getAttribute('data-ca-profile-name') ||
                    el.getAttribute('title') || el.innerText || el.textContent || '');
                // Take first non-empty line
                t = t.split('\n').map(clean).find(Boolean) || t;
                if (looksLikeName(t)) {
                    out.name = t;
                    out.source = 'dom:' + sel;
                    // user id from href
                    try {
                        const href = el.getAttribute('href') || '';
                        const m = href.match(/user_id=(\d+)/i);
                        if (m) out.user_id = m[1];
                    } catch (e) {}
                    return out;
                }
            }
        }

        // Last: any profiles.update with readable text
        try {
            const links = Array.from(document.querySelectorAll('a[href*="user_id="]'));
            for (const a of links) {
                const t = clean(a.innerText || a.textContent || '');
                if (looksLikeName(t)) {
                    out.name = t;
                    out.source = 'profiles_link';
                    const m = (a.getAttribute('href') || '').match(/user_id=(\d+)/i);
                    if (m) out.user_id = m[1];
                    return out;
                }
            }
        } catch (e) {}

        return out;
    })(),
    has_main_image_input: !!document.querySelector('input[type="file"][name*="product_main"], input[type="file"][name*="type_main"], input[type="file"].cm-image-field'),
    has_file_inputs: document.querySelectorAll('input[type="file"]').length
};
"""

# Applies structured AI payload. payload is injected as JSON by Python.
FILL_SCRIPT_TEMPLATE = r"""
const data = __PAYLOAD__;

function fire(el) {
    try { el.focus(); } catch (e) {}
    try {
        el.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertReplacementText' }));
    } catch (e) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    if (window.jQuery) {
        try { jQuery(el).trigger('input').trigger('change').trigger('blur').trigger('keyup'); } catch (e) {}
    }
}

function setNativeValue(el, str) {
    const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, str);
    else el.value = str;
}

function findAll(selectors) {
    const out = [];
    const seen = new Set();
    for (const selector of selectors) {
        try {
            document.querySelectorAll(selector).forEach((el) => {
                if (seen.has(el)) return;
                seen.add(el);
                out.push(el);
            });
        } catch (e) {}
    }
    return out;
}

function flash(el) {
    try {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        const prev = el.style.outline;
        el.style.outline = '3px solid #00a3d9';
        setTimeout(() => { el.style.outline = prev; }, 2500);
    } catch (e) {}
}

function fillNative(value, selectors) {
    if (value === undefined || value === null || value === '') {
        return { ok: false, reason: 'empty_ai_value', preview: '', selector: '' };
    }
    const str = String(value);
    const els = findAll(selectors);
    if (!els.length) {
        return { ok: false, reason: 'selector_not_found', preview: '', selector: selectors[0] || '' };
    }
    let last = null;
    for (const el of els) {
        if (el.disabled) continue;
        setNativeValue(el, str);
        fire(el);
        last = el;
    }
    if (!last) return { ok: false, reason: 'elements_disabled', preview: '', selector: '' };
    flash(last);
    const readBack = (last.value || '').slice(0, 80);
    return {
        ok: readBack.length > 0 || str.length === 0,
        reason: 'filled',
        preview: readBack,
        selector: last.id ? ('#' + last.id) : (last.name || last.tagName)
    };
}

function setWysiwyg(el, html) {
    let changed = false;
    const $ = window.jQuery;

    if ($ && $.fn && typeof $.fn.ceEditor === 'function') {
        try { $(el).ceEditor('val', html); changed = true; } catch (e) {}
        try { $.ceEditor('val', $(el), html); changed = true; } catch (e) {}
    }
    if ($ && $.fn && $.fn.redactor) {
        try {
            const api = $(el).data('redactor');
            if (api && api.code && typeof api.code.set === 'function') {
                api.code.set(html); changed = true;
            } else {
                $(el).redactor('code.set', html); changed = true;
            }
        } catch (e) {}
        try { $(el).redactor('set', html); changed = true; } catch (e) {}
    }
    if (typeof window.$R === 'function' && el.id) {
        try { window.$R('#' + el.id, 'source.setCode', html); changed = true; } catch (e) {}
        try { window.$R('#' + el.id, 'source.set', html); changed = true; } catch (e) {}
    }
    if (window.tinymce) {
        const id = el.id;
        let editor = id ? window.tinymce.get(id) : null;
        if (!editor && window.tinymce.editors) {
            editor = Array.from(window.tinymce.editors).find(e => e.targetElm === el);
        }
        if (editor) { editor.setContent(html); editor.save(); changed = true; }
    }
    if (window.CKEDITOR && window.CKEDITOR.instances) {
        for (const [name, editor] of Object.entries(window.CKEDITOR.instances)) {
            const element = editor.element && editor.element.$;
            if (element === el || name === el.id) {
                editor.setData(html); editor.updateElement(); changed = true; break;
            }
        }
    }
    try {
        const box = el.closest('.redactor-box') || el.parentElement;
        if (box) {
            const editable = box.querySelector('.redactor-editor, .redactor-in, [contenteditable="true"]');
            if (editable) { editable.innerHTML = html; changed = true; }
        }
        let next = el.nextElementSibling;
        for (let i = 0; i < 4 && next; i++, next = next.nextElementSibling) {
            const editable = next.matches && next.matches('[contenteditable="true"], .redactor-editor, .redactor-in')
                ? next
                : next.querySelector && next.querySelector('[contenteditable="true"], .redactor-editor, .redactor-in');
            if (editable) { editable.innerHTML = html; changed = true; break; }
        }
    } catch (e) {}

    setNativeValue(el, html);
    fire(el);
    return changed;
}

function fillRich(value, selectors) {
    if (value === undefined || value === null || value === '') {
        return { ok: false, reason: 'empty_ai_value', preview: '', selector: '' };
    }
    const str = String(value);
    const els = findAll(selectors);
    if (!els.length) {
        return { ok: false, reason: 'selector_not_found', preview: '', selector: selectors.join(' | ') };
    }
    let last = null;
    for (const el of els) {
        setWysiwyg(el, str);
        last = el;
    }
    flash(last);
    return {
        ok: true,
        reason: 'filled_rich',
        preview: (last.value || str).slice(0, 80),
        selector: last.id ? ('#' + last.id) : (last.name || last.tagName)
    };
}

function normalize(s) {
    return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();
}

function textOf(el) {
    return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
}

// ---- open product-page-only feature/video tab panes (never main menu) ----
(function openTabs() {
    const keywords = ['feature', 'spec', 'მახასიათ', 'თვისებ', 'სპეციფ', 'video', 'ვიდეო', 'gallery', 'გალერ', 'ab:', 'categor', 'კატეგ', 'კატეგორ'];
    const root =
        document.querySelector('form[name="product_update_form"]') ||
        document.querySelector('#product_update_form') ||
        document.querySelector('#content_management') ||
        document.body;
    const nodes = root.querySelectorAll(
        '.cm-j-tabs a, .cm-tabs a, .nav-tabs a, .tabs a, a[href^="#content_"], a[data-ca-target-id], a.cm-js, li.cm-js > a'
    );
    nodes.forEach(el => {
        if (el.closest('#header_navbar, #header_subnav, .navbar, .sidebar, #menu, .top-menu')) return;
        const href = (el.getAttribute('href') || '');
        if (href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript:')) return;
        if (href.toLowerCase().includes('product_features')) return;
        if (href.toLowerCase().includes('dispatch=')) return;
        const t = normalize(textOf(el));
        if (keywords.some(k => t.includes(k)) || (href.startsWith('#content_'))) {
            try { el.click(); } catch (e) {}
        }
    });
    ['content_features', 'content_product_features', 'content_categories', 'content_category',
     'content_ab__video_gallery', 'content_ab_video_gallery', 'content_video_gallery', 'content_detailed'
    ].forEach(id => {
        const n = document.getElementById(id);
        if (n) { n.classList.remove('hidden', 'collapsed'); n.style.display = ''; }
    });
})();

// Add video only inside product form video section — not global nav
(function tryAddVideo() {
    const roots = [
        document.getElementById('content_ab__video_gallery'),
        document.getElementById('content_ab_video_gallery'),
        document.getElementById('content_video_gallery'),
        document.querySelector('[id*="ab__video"]'),
        document.querySelector('form[name="product_update_form"]')
    ].filter(Boolean);
    for (const root of roots) {
        const buttons = Array.from(root.querySelectorAll('a, button, .btn, .cm-add, .cm-add-item'));
        for (const b of buttons) {
            const href = (b.getAttribute('href') || '');
            if (href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript:') && !b.classList.contains('cm-ajax')) {
                // allow cm-ajax adders; block plain navigations
                if (href.includes('dispatch=') || href.includes('.php')) continue;
            }
            const t = normalize(textOf(b));
            if (
                t.includes('add video') || t.includes('new video') ||
                (t.includes('add') && t.includes('video')) ||
                (t.includes('დამატ') && t.includes('ვიდეო')) ||
                t.includes('добавить видео')
            ) {
                try { b.click(); } catch (e) {}
                return;
            }
        }
        const multi = root.querySelector('.cm-add-item, .cm-add');
        if (multi) { try { multi.click(); } catch (e) {} return; }
    }
})();

const results = {
    product_name: { ok: false },
    price: { ok: false },
    old_price: { ok: false },
    tags: { ok: false },
    full_description: { ok: false },
    promo_text: { ok: false },
    page_title: { ok: false },
    meta_description: { ok: false },
    meta_keywords: { ok: false },
    seo_name: { ok: false },
    features: { attempted: 0, matched: 0, details: [], fields_on_page: 0 },
    categories: { attempted: 0, matched: 0, note: '' },
    videos: { attempted: 0, matched: 0, note: '', fields_on_page: 0 },
    images: { note: 'File/image uploads must be done manually in CS-Cart.' },
    debug: {}
};

results.product_name = fillNative(data.product_name, [
    '#product_description_product',
    'input[name="product_data[product]"]'
]);

results.price = fillNative(data.price, [
    '#elm_price_price',
    'input[name="product_data[price]"]'
]);

results.old_price = fillNative(data.old_price, [
    '#elm_price_list_price',
    'input[name="product_data[list_price]"]',
    'input[name="product_data[old_price]"]'
]);

// Tags: CS-Cart tags input (Select2-based or plain input)
if (data.tags) {
    const tagStr = String(data.tags || '').trim();
    if (tagStr) {
        const tagInputs = Array.from(document.querySelectorAll(
            'input[name*="tags"], input[name*="product_tags"], ' +
            'input[name="product_data[tags]"], .tags input, ' +
            'select[name*="tags"], .cm-tags input'
        ));
        let tagFilled = false;
        for (const inp of tagInputs) {
            if (inp.tagName === 'SELECT') {
                const tags = tagStr.split(',').map(t => t.trim()).filter(Boolean);
                for (const tag of tags) {
                    let opt = Array.from(inp.options).find(o => o.text === tag);
                    if (!opt) {
                        opt = new Option(tag, tag, true, true);
                        inp.add(opt);
                    }
                    opt.selected = true;
                }
                inp.dispatchEvent(new Event('change', { bubbles: true }));
                tagFilled = true;
            } else {
                inp.value = tagStr;
                inp.dispatchEvent(new Event('input', { bubbles: true }));
                inp.dispatchEvent(new Event('change', { bubbles: true }));
                tagFilled = true;
            }
        }
        if (!tagFilled) {
            const s2tag = document.querySelector('.select2-selection--tags, .tag-input, .tags-field input');
            if (s2tag) {
                s2tag.value = tagStr;
                s2tag.dispatchEvent(new Event('input', { bubbles: true }));
                s2tag.dispatchEvent(new Event('change', { bubbles: true }));
                tagFilled = true;
            }
        }
        results.tags = { ok: tagFilled, preview: tagStr.slice(0, 60) };
    } else {
        results.tags = { ok: false, reason: 'empty_ai_value' };
    }
} else {
    results.tags = { ok: false };
}

results.full_description = fillRich(
    data.full_description,
    [
        'textarea#elm_product_full_descr',
        'textarea#elm_full_descr',
        'textarea[name="product_data[full_description]"]',
        'textarea[name*="[full_description]"]',
        'textarea.cm-wysiwyg[name*="full_description"]'
    ]
);

results.promo_text = fillRich(
    data.promo_text,
    [
        'textarea#elm_product_promo_text',
        'textarea#elm_promo_text',
        'textarea[name="product_data[promo_text]"]',
        'textarea[name*="[promo_text]"]',
        'textarea.cm-wysiwyg[name*="promo_text"]'
    ]
);

results.page_title = fillNative(data.page_title, [
    '#elm_product_page_title',
    'input[name="product_data[page_title]"]'
]);

results.meta_description = fillNative(data.meta_description, [
    '#elm_product_meta_descr',
    'textarea[name="product_data[meta_description]"]'
]);

results.meta_keywords = fillNative(data.meta_keywords, [
    '#elm_product_meta_keywords',
    'textarea[name="product_data[meta_keywords]"]'
]);

results.seo_name = fillNative(data.seo_name, [
    '#elm_seo_name',
    'input[name="product_data[seo_name]"]',
    'input[name="seo_name"]',
    'input[name*="[seo_name]"]'
]);

// ---------- FEATURES / SPECIFICATIONS ----------
function collectFeatureFields() {
    return Array.from(document.querySelectorAll(
        'select[name*="product_features"], input[name*="product_features"], textarea[name*="product_features"], ' +
        'select[name*="feature_data"], input[name*="feature_data"], ' +
        '#content_features select, #content_features input:not([type="hidden"]):not([type="file"]), #content_features textarea, ' +
        '#content_product_features select, #content_product_features input:not([type="hidden"]):not([type="file"]), #content_product_features textarea, ' +
        '[id*="content_feature"] select, [id*="content_feature"] input:not([type="hidden"]):not([type="file"]), ' +
        'select[name*="variant"], input[name*="[variants]"]'
    )).filter(el => {
        const n = (el.getAttribute('name') || '').toLowerCase();
        const t = (el.type || '').toLowerCase();
        if (t === 'hidden' || t === 'file' || t === 'submit') return false;
        if (n.includes('category')) return false;
        if (n.includes('feature') || n.includes('variant')) return true;
        if (el.closest('#content_features, #content_product_features, [id*="content_feature"], .features')) return true;
        return false;
    });
}

function labelFor(el) {
    const group = el.closest('.control-group, .ty-control-group, tr, .feature-item, li, .cm-feature');
    if (group) {
        const lab = group.querySelector('label, .control-label, th, .feature-name, td:first-child');
        if (lab) return textOf(lab);
    }
    if (el.id) {
        const lab = document.querySelector('label[for="' + el.id.replace(/"/g, '') + '"]');
        if (lab) return textOf(lab);
    }
    return '';
}

function valuesList(raw) {
    if (raw === undefined || raw === null || raw === '') return [];
    if (Array.isArray(raw)) return raw.map(v => String(v)).filter(v => v !== '');
    return [String(raw)];
}

function optionMatches(opt, want) {
    const w = normalize(want);
    if (!w) return false;
    const ov = String(opt.value || '');
    const otRaw = String(opt.textContent || opt.label || opt.getAttribute('data-asf-label') || '').trim();
    // Ignore placeholder / empty / "..." option text when matching by label
    if (otRaw === '...' || otRaw === '…' || otRaw === '-' || otRaw === '×') {
        return ov !== '' && ov === String(want);
    }
    const ot = normalize(otRaw);
    if (ov === String(want)) return true;
    if (ot === w) return true;
    if (ot && ot.length > 1 && w.length > 1 && (ot.includes(w) || w.includes(ot))) return true;
    return false;
}

function isBadSelectText(t) {
    const s = String(t == null ? '' : t).replace(/[\u00d7]/g, '').trim();
    return !s || s === '...' || s === '\u2026' || s === '-';
}

function pickHumanLabel(wantList, optionText) {
    const humans = (wantList || []).filter(w => {
        const s = String(w || '').trim();
        return s && !/^\d+$/.test(s) && !isBadSelectText(s) && s.length > 1;
    });
    if (humans.length) return String(humans[0]);
    const ot = String(optionText || '').trim();
    if (!isBadSelectText(ot) && !/^\d+$/.test(ot)) return ot;
    return '';
}

/** Roots that belong ONLY to this <select> (never sibling features). */
function select2RootsFor(el) {
    const roots = [];
    const jq = window.jQuery || window.$ || null;
    const tq = (window.Tygh && Tygh.$) || null;
    const tryData = ($) => {
        try {
            if ($ && $(el).data && $(el).data('select2')) {
                const s2 = $(el).data('select2');
                if (s2.$selection && s2.$selection[0]) roots.push(s2.$selection[0]);
                if (s2.$container && s2.$container[0]) roots.push(s2.$container[0]);
            }
        } catch (e) {}
    };
    tryData(jq);
    tryData(tq);
    // Select2 4 id containers
    try {
        const eid = el.id || '';
        if (eid) {
            const cont = document.getElementById('select2-' + eid + '-container');
            if (cont) {
                roots.push(cont);
                if (cont.closest) {
                    const box = cont.closest('.select2-container, .select2');
                    if (box) roots.push(box);
                }
            }
        }
        // Also match any select2 selection tied via aria
        if (eid) {
            document.querySelectorAll(
                '[id*="select2-' + eid + '"], [aria-labelledby*="select2-' + eid + '"]'
            ).forEach(n => roots.push(n));
        }
    } catch (e) {}
    try {
        let sib = el.nextElementSibling;
        let hops = 0;
        while (sib && hops < 4) {
            const cn = String(sib.className || '');
            if (/select2|object-picker/.test(cn)) {
                roots.push(sib);
                break;
            }
            if (sib.tagName === 'SELECT' || sib.tagName === 'INPUT') break;
            sib = sib.nextElementSibling;
            hops++;
        }
    } catch (e) {}
    try {
        const wrap = el.closest(
            '.object-picker, .cm-object-picker, .cm-field-container, .controls, .control-group, .ty-control-group, tr'
        );
        if (wrap) {
            const selects = wrap.querySelectorAll('select');
            // Only attach to shared group when this is the sole product-feature select in it
            if (selects.length === 1 && selects[0] === el) roots.push(wrap);
        }
    } catch (e) {}
    // de-dupe
    return Array.from(new Set(roots.filter(Boolean)));
}

function writeSelect2Label(node, display) {
    if (!node || !display) return;
    try { node.setAttribute('title', display); } catch (e) {}
    const choiceDisplay = node.querySelector && node.querySelector('.select2-selection__choice__display');
    if (choiceDisplay) {
        choiceDisplay.textContent = display;
        return;
    }
    const clear = node.querySelector && node.querySelector('.select2-selection__clear');
    const remove = node.querySelector && node.querySelector('.select2-selection__choice__remove');
    if (clear) {
        node.innerHTML = '';
        node.appendChild(clear);
        node.appendChild(document.createTextNode(display));
    } else if (remove) {
        node.innerHTML = '';
        node.appendChild(remove);
        node.appendChild(document.createTextNode(display));
    } else if (!(node.querySelector && node.querySelector('input'))) {
        node.textContent = display;
    }
}

/** Force THIS field's visible Select2 text (scoped — does not touch sibling features). */
function patchSelect2Display(el, ids, texts) {
    if (!el) return;
    const display = (texts && texts[0]) || '';
    if (!display || isBadSelectText(display)) return;
    const roots = select2RootsFor(el);
    if (!roots.length && el.nextElementSibling) roots.push(el.nextElementSibling);

    roots.forEach(root => {
        if (!root || !root.querySelectorAll) return;
        const nodes = root.querySelectorAll(
            '.select2-selection__rendered, .select2-selection__choice, ' +
            '.object-picker__selection-text, .cm-object-picker-selected'
        );
        if (!nodes.length && root.classList &&
            /selection__rendered/.test(root.className || '')) {
            writeSelect2Label(root, display);
            return;
        }
        nodes.forEach(node => {
            if (node.tagName === 'INPUT') return;
            if (node.classList && node.classList.contains('select2-search__field')) return;
            writeSelect2Label(node, display);
        });
        if (el.multiple && texts && texts.length) {
            Array.from(root.querySelectorAll('.select2-selection__choice')).forEach((chip, idx) => {
                const lab = texts[idx] || texts[0];
                if (lab && !isBadSelectText(lab)) writeSelect2Label(chip, lab);
            });
        }
    });
}

/** Set a <select> (incl. Select2 / CS-Cart object-picker) to one or more variant ids/labels. */
function applySelectValues(el, wants) {
    const wantList = valuesList(wants);
    if (!wantList.length) return false;

    const humanPreferred = pickHumanLabel(wantList, '');
    const resolvedIds = [];
    const resolvedTexts = [];

    wantList.forEach(want => {
        let option = Array.from(el.options || []).find(o => optionMatches(o, want));
        if (!option) {
            if (!/^\d+$/.test(String(want))) return;
            option = document.createElement('option');
            option.value = String(want);
            option.textContent = humanPreferred || String(want);
            try { option.setAttribute('data-asf-label', option.textContent); } catch (e) {}
            el.appendChild(option);
        }
        const rawText = String(
            option.getAttribute('data-asf-label') || option.textContent || option.label || ''
        ).trim();
        let display = humanPreferred || pickHumanLabel(wantList, rawText) || rawText;
        if (isBadSelectText(display) || (/^\d+$/.test(display) && humanPreferred)) {
            display = humanPreferred || rawText || String(want);
        }
        if (!isBadSelectText(display) && !/^\d+$/.test(String(display))) {
            try {
                option.textContent = display;
                option.setAttribute('label', display);
                option.setAttribute('data-asf-label', display);
            } catch (e) {}
        }
        resolvedIds.push(String(option.value));
        resolvedTexts.push(String(display || option.value || want).trim());
    });

    if (!resolvedIds.length) {
        wantList.forEach(want => {
            const w = normalize(want);
            if (!w || /^\d+$/.test(String(want)) || isBadSelectText(want)) return;
            const option = Array.from(el.options || []).find(o => {
                const ot = normalize(
                    o.getAttribute('data-asf-label') || o.textContent || o.label || ''
                );
                return ot && !isBadSelectText(ot) && (ot === w || ot.includes(w) || w.includes(ot));
            });
            if (option) {
                const display = humanPreferred || String(want);
                try {
                    option.textContent = display;
                    option.setAttribute('data-asf-label', display);
                } catch (e) {}
                resolvedIds.push(String(option.value));
                resolvedTexts.push(display);
            }
        });
    }

    if (!resolvedIds.length && humanPreferred) {
        const numId = wantList.find(w => /^\d+$/.test(String(w)));
        if (numId) {
            let option = Array.from(el.options || []).find(o => String(o.value) === String(numId));
            if (!option) {
                option = document.createElement('option');
                option.value = String(numId);
                el.appendChild(option);
            }
            option.textContent = humanPreferred;
            try { option.setAttribute('data-asf-label', humanPreferred); } catch (e) {}
            resolvedIds.push(String(numId));
            resolvedTexts.push(humanPreferred);
        }
    }

    if (!resolvedIds.length) return false;

    const ids = [];
    const texts = [];
    resolvedIds.forEach((id, i) => {
        if (ids.indexOf(id) >= 0) return;
        if (!String(id).trim() && !String(resolvedTexts[i] || '').trim()) return;
        ids.push(id);
        let t = resolvedTexts[i] || humanPreferred || '';
        if (isBadSelectText(t) || (/^\d+$/.test(t) && humanPreferred)) t = humanPreferred || t;
        texts.push(t);
    });
    if (!ids.length) return false;

    const human = humanPreferred || texts.find(t => t && !isBadSelectText(t) && !/^\d+$/.test(t)) || '';

    ids.forEach((id, i) => {
        const display = texts[i] || human || id;
        let opt = Array.from(el.options || []).find(o => String(o.value) === String(id));
        if (!opt) {
            try { opt = new Option(display, id, true, true); }
            catch (e) {
                opt = document.createElement('option');
                opt.value = id;
                opt.text = display;
                opt.selected = true;
            }
            el.appendChild(opt);
        } else {
            if (human || (texts[i] && !isBadSelectText(texts[i]))) {
                opt.textContent = display;
                try { opt.setAttribute('data-asf-label', display); } catch (e) {}
            }
            opt.selected = true;
        }
    });

    try {
        if (el.multiple) {
            Array.from(el.options).forEach(o => {
                o.selected = ids.indexOf(String(o.value)) >= 0;
            });
        } else {
            Array.from(el.options).forEach(o => { o.selected = false; });
            const id = ids[0];
            const opt = Array.from(el.options).find(o => String(o.value) === String(id));
            if (opt) {
                opt.selected = true;
                if (human) {
                    opt.textContent = String(human);
                    try { opt.setAttribute('data-asf-label', human); } catch (e) {}
                }
            }
            try { el.value = id; } catch (e) {}
        }
    } catch (e) {}

    fire(el);

    // Official Select2 multi/single: Option(text, id, true, true) then one change
    try {
        const display0 = human || texts[0] || ids[0];
        const applyWith = ($lib) => {
            if (!$lib || !$lib(el).length) return;
            if (el.multiple) {
                ids.forEach((id, i) => {
                    const display = texts[i] || human || id;
                    let $opt = $lib(el).find('option').filter(function () {
                        return String(this.value) === String(id);
                    });
                    if (!$opt.length) {
                        try { $lib(el).append(new Option(display, id, true, true)); }
                        catch (e) {}
                    } else {
                        $opt.prop('selected', true).text(display);
                    }
                });
                $lib(el).val(ids).trigger('change');
            } else {
                const id = ids[0];
                let $opt = $lib(el).find('option').filter(function () {
                    return String(this.value) === String(id);
                }).first();
                if (!$opt.length) {
                    try { $lib(el).append(new Option(display0, id, true, true)); }
                    catch (e) {
                        const o = document.createElement('option');
                        o.value = id; o.text = display0; o.selected = true;
                        el.appendChild(o);
                    }
                } else {
                    $opt.text(display0).prop('selected', true);
                }
                $lib(el).val(String(id)).trigger('change');
            }
            try { $lib(el).trigger('change.select2'); } catch (e) {}
        };
        const jq = window.jQuery || window.$ || null;
        const tq = (window.Tygh && Tygh.$) || null;
        // Use exactly one jQuery handle — dual triggers can blank object-picker to "..."
        applyWith(tq || jq);
    } catch (e) {}

    try {
        patchSelect2Display(el, ids, texts.map((t, i) => t || human || ids[i]));
    } catch (e) {}

    try {
        window.__ASF_FEATURE_FILL = window.__ASF_FEATURE_FILL || [];
        window.__ASF_FEATURE_FILL.push({
            name: el.getAttribute('name') || '',
            id: el.id || '',
            ids: ids.slice(),
            texts: texts.map((t, i) => t || human || ids[i])
        });
    } catch (e) {}

    if (el.multiple) {
        const selected = Array.from(el.selectedOptions || []).map(o => String(o.value));
        return ids.some(id => selected.indexOf(String(id)) >= 0);
    }
    // Value set OR option is selected counts as success even if Select2 UI lags
    try {
        if (String(el.value) === String(ids[0])) return true;
        if (ids.indexOf(String(el.value)) >= 0) return true;
        const sel = Array.from(el.selectedOptions || []).map(o => String(o.value));
        if (ids.some(id => sel.indexOf(String(id)) >= 0)) return true;
    } catch (e) {}
    return false;
}

function applyValuesToField(el, rawValue) {
    const wants = valuesList(rawValue);
    if (!wants.length) return false;

    if (el.tagName === 'SELECT') {
        return applySelectValues(el, wants);
    }

    if (el.type === 'radio') {
        const name = el.getAttribute('name') || '';
        const radios = name
            ? Array.from(document.querySelectorAll('input[type="radio"][name="' + name.replace(/"/g, '\\"') + '"]'))
            : [el];
        for (const r of radios) {
            if (wants.some(w => optionMatches({ value: r.value, textContent: labelFor(r) }, w) || String(r.value) === String(w))) {
                r.checked = true;
                fire(r);
                return true;
            }
        }
        return false;
    }

    if (el.type === 'checkbox') {
        // Handle whole name group
        const name = el.getAttribute('name') || '';
        let boxes = [el];
        if (name) {
            const esc = name.replace(/"/g, '\\"');
            const same = Array.from(document.querySelectorAll('input[type="checkbox"][name="' + esc + '"]'));
            if (same.length) boxes = same;
            // also [] array variants for multi features
            const base = name.replace(/\[\d+\]$/, '[]');
            if (base !== name) {
                document.querySelectorAll('input[type="checkbox"]').forEach(b => {
                    const n = b.getAttribute('name') || '';
                    if (n === base || n.replace(/\[\d+\]$/, '[]') === base) {
                        if (!boxes.includes(b)) boxes.push(b);
                    }
                });
            }
            // product_features[id][variants] style siblings in same group
            const group = el.closest('.control-group, .ty-control-group, tr, .feature-item, li, fieldset');
            if (group) {
                group.querySelectorAll('input[type="checkbox"]').forEach(b => {
                    if (!boxes.includes(b)) boxes.push(b);
                });
            }
        }
        let any = false;
        boxes.forEach(b => {
            let lab = labelFor(b);
            if (!lab && b.id) {
                const l = document.querySelector('label[for="' + b.id + '"]');
                if (l) lab = textOf(l);
            }
            const hit = wants.some(w =>
                String(b.value) === String(w) ||
                optionMatches({ value: b.value, textContent: lab }, w)
            );
            if (hit !== b.checked) {
                b.click();
            } else if (hit) {
                b.checked = true;
                fire(b);
            }
            if (hit) any = true;
        });
        // uncheck unmatched when multi requested explicit list
        if (any && wants.length) {
            boxes.forEach(b => {
                let lab = labelFor(b);
                const hit = wants.some(w =>
                    String(b.value) === String(w) ||
                    optionMatches({ value: b.value, textContent: lab }, w)
                );
                if (!hit && b.checked) {
                    b.click();
                }
            });
        }
        return any;
    }

    // text / textarea — use first value
    setNativeValue(el, wants[0]);
    fire(el);
    return true;
}

// Back-compat alias
function applyValueToField(el, rawValue) {
    return applyValuesToField(el, rawValue);
}

try { window.__ASF_FEATURE_FILL = []; } catch (e) {}
const featureFields = collectFeatureFields();
results.features.fields_on_page = featureFields.length;

// Build work list from feature_values array + features map
const work = [];
if (Array.isArray(data.feature_values)) {
    data.feature_values.forEach(item => {
        if (!item || typeof item !== 'object') return;
        // Prefer variant IDs (values) and also include human labels so Select2 can match either
        const parts = [];
        if (Array.isArray(item.values)) item.values.forEach(v => parts.push(v));
        else if (item.values != null && item.values !== '') parts.push(item.values);
        if (Array.isArray(item.value)) item.value.forEach(v => parts.push(v));
        else if (item.value != null && item.value !== '') parts.push(item.value);
        if (Array.isArray(item.labels)) item.labels.forEach(v => parts.push(v));
        // unique non-empty strings, IDs first (kept order from values)
        const seen = new Set();
        const combined = [];
        parts.forEach(v => {
            const s = String(v == null ? '' : v).trim();
            if (!s || seen.has(s)) return;
            seen.add(s);
            combined.push(s);
        });
        if (!combined.length) return;
        work.push({
            id: String(item.id || ''),
            field_name: String(item.field_name || ''),
            label: String(item.label || item.name || ''),
            value: combined,
            selection_mode: String(item.selection_mode || '')
        });
    });
}
if (data.features && typeof data.features === 'object' && !Array.isArray(data.features)) {
    Object.entries(data.features).forEach(([label, value]) => {
        if (!work.some(w => normalize(w.label) === normalize(label))) {
            work.push({ id: '', field_name: '', label, value });
        }
    });
}
results.features.attempted = work.length;

work.forEach(item => {
    let matched = false;
    let how = '';
    const tryEl = (el, howTag) => {
        if (!el) return false;
        if (applyValuesToField(el, item.value)) {
            matched = true; how = howTag; flash(el); return true;
        }
        return false;
    };
    // 1) exact field_name
    if (item.field_name) {
        let el = featureFields.find(f => f.getAttribute('name') === item.field_name);
        if (!el) {
            // querySelector can't handle unescaped [] well — scan all fields
            el = Array.from(document.querySelectorAll('select, input, textarea')).find(
                f => (f.getAttribute('name') || '') === item.field_name
            ) || null;
        }
        tryEl(el, 'field_name');
        // checkbox group: match any same-base name
        if (!matched) {
            for (const f of featureFields) {
                const n = f.getAttribute('name') || '';
                if (n === item.field_name || n.replace(/\[\d+\]$/, '[]') === item.field_name.replace(/\[\d+\]$/, '[]')) {
                    if (tryEl(f, 'field_name_group')) break;
                }
            }
        }
        // Close-match: field_name may be product_data[product_features][12][variant_id]
        // vs page has product_data[product_features][12][variant_id][] or without product_data
        if (!matched && item.field_name) {
            const core = item.field_name.replace(/^product_data/, '').replace(/\[\]$/,'');
            for (const f of featureFields) {
                const n = f.getAttribute('name') || '';
                if (n === item.field_name) continue;
                if (n.includes(core) || core.includes(n) ||
                    (item.id && n.includes('[' + item.id + ']')) ) {
                    if (tryEl(f, 'field_name_fuzzy')) break;
                }
            }
        }
    }
    // 2) id in name
    if (!matched && item.id) {
        const el = featureFields.find(f => (f.getAttribute('name') || '').includes('[' + item.id + ']'));
        tryEl(el, 'id');
        if (!matched) {
            for (const f of featureFields) {
                if ((f.getAttribute('name') || '').includes('[' + item.id + ']')) {
                    if (tryEl(f, 'id_group')) break;
                }
            }
        }
    }
    // 3) label text
    if (!matched && item.label) {
        const target = normalize(item.label);
        for (const el of featureFields) {
            const lab = normalize(labelFor(el));
            if (!lab) continue;
            if (lab === target || lab.includes(target) || target.includes(lab)) {
                if (tryEl(el, 'label')) break;
            }
        }
    }
    results.features.details.push({
        feature: item.label || item.field_name || item.id,
        value: item.value,
        matched,
        how
    });
    if (matched) results.features.matched += 1;
});

// Final pass: re-stamp every feature Select2 label (async handlers often reset to "...")
try {
    const stash = Array.isArray(window.__ASF_FEATURE_FILL) ? window.__ASF_FEATURE_FILL : [];
    stash.forEach(entry => {
        let el = null;
        if (entry.name) {
            el = Array.from(document.querySelectorAll('select')).find(
                s => (s.getAttribute('name') || '') === entry.name
            ) || null;
        }
        if (!el && entry.id) {
            const byId = document.getElementById(entry.id);
            if (byId && byId.tagName === 'SELECT') el = byId;
        }
        if (!el) return;
        const ids = entry.ids || [];
        const texts = entry.texts || [];
        ids.forEach((id, i) => {
            const display = texts[i] || texts[0] || '';
            if (!display || isBadSelectText(display)) return;
            let opt = Array.from(el.options || []).find(o => String(o.value) === String(id));
            if (!opt) {
                try { opt = new Option(display, id, true, true); el.appendChild(opt); }
                catch (e) {}
            } else {
                opt.textContent = display;
                opt.selected = true;
            }
        });
        try {
            if (el.multiple) {
                Array.from(el.options || []).forEach(o => {
                    o.selected = ids.indexOf(String(o.value)) >= 0;
                });
            } else if (ids[0]) {
                try { el.value = ids[0]; } catch (e) {}
            }
        } catch (e) {}
        try { patchSelect2Display(el, ids, texts); } catch (e) {}
    });
} catch (e) {}

if (work.length === 0 && featureFields.length > 0) {
    results.features.note = 'AI returned no feature values, but ' + featureFields.length + ' feature fields exist on page.';
} else if (work.length > 0 && featureFields.length === 0) {
    results.features.note = 'No feature form fields found. Open the Features/Specifications tab on the product page and run again.';
}

// ---------- CATEGORIES (product object-picker / Select2 multi) ----------
const wantedCats = Array.isArray(data.categories) ? data.categories : [];
results.categories.attempted = wantedCats.length;
let catMatched = 0;
const catWantIds = new Set();
const catWantLabels = [];

// Keep id+label together so Select2 shows real names (not "...")
const catWantItems = [];
wantedCats.forEach((c) => {
    if (c && typeof c === 'object') {
        let id = String(
            (c.value != null && String(c.value) !== '') ? c.value :
            (c.id != null && String(c.id) !== '' ? c.id : '')
        ).trim();
        let label = String(c.label || c.name || '').trim();
        if (id) catWantIds.add(id);
        if (label) catWantLabels.push(normalize(label));
        if (!id && !label) return;
        catWantItems.push({
            id: id,
            label: label,
            field_name: String(c.field_name || '')
        });
    } else if (c != null && String(c).trim()) {
        const s = String(c).trim();
        catWantIds.add(s);
        catWantLabels.push(normalize(s));
        catWantItems.push({
            id: /^\d+$/.test(s) ? s : '',
            label: s,
            field_name: ''
        });
    }
});

function isProductCategorySelectFill(el) {
    if (!el || el.tagName !== 'SELECT') return false;
    const n = (el.getAttribute('name') || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    if (n.includes('product_data') && n.includes('category')) return true;
    if (n.includes('category_ids') && !n.includes('company') && !n.includes('storefront')) return true;
    if (id.includes('product_categor')) return true;
    const group = el.closest('.control-group, .ty-control-group, .form-group, tr');
    if (group) {
        const lab = group.querySelector('label, .control-label');
        const t = ((lab && (lab.innerText || lab.textContent)) || '').toLowerCase();
        if (t.includes('კატეგორ') || (t.includes('categor') && !t.includes('feature'))) return true;
    }
    return false;
}

function fillProductCategorySelect(sel, items) {
    if (!sel || !items || !items.length) return 0;
    const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$) || null;

    try {
        if (!sel.multiple && (sel.getAttribute('name') || '').includes('[]')) {
            sel.multiple = true;
        }
    } catch (e) {}

    const resolved = [];
    const seenId = new Set();
    items.forEach(it => {
        let id = String(it.id || '').trim();
        let label = String(it.label || '').trim();
        if (label === '...' || label === '…' || label === '×') label = '';

        let opt = null;
        if (id) {
            opt = Array.from(sel.options || []).find(o => String(o.value) === String(id));
        }
        if (!opt && label) {
            opt = Array.from(sel.options || []).find(o => optionMatches(o, label));
            if (opt) id = String(opt.value || id);
        }
        if (opt) {
            const ot = String(opt.getAttribute('data-asf-label') || opt.textContent || '').trim();
            if ((!label || label === id || /^\d+$/.test(label)) && ot && ot !== '...' && ot !== '…') {
                label = ot.split('\n')[0].trim();
            }
        }
        if (!label && id && !/^\d+$/.test(id)) label = id;
        if (!id) return;
        if (seenId.has(id)) return;
        // Require a real display name so Select2 does not render "..."
        if (!label || label === '...' || label === '…') return;
        seenId.add(id);
        resolved.push({ id: id, label: label });
    });
    if (!resolved.length) return 0;

    // Clear previous selection
    try {
        Array.from(sel.options || []).forEach(o => { o.selected = false; });
        if ($ && $(sel).length) {
            try { $(sel).val(null).trigger('change'); } catch (e) {}
        }
    } catch (e) {}

    const ids = [];
    resolved.forEach(r => {
        let opt = Array.from(sel.options || []).find(o => String(o.value) === String(r.id));
        if (!opt) {
            try {
                opt = new Option(r.label, r.id, true, true);
            } catch (e2) {
                opt = document.createElement('option');
                opt.value = r.id;
                opt.text = r.label;
                opt.selected = true;
            }
            sel.appendChild(opt);
        } else {
            // Force real name onto option (Select2 reads this for chips)
            opt.textContent = r.label;
            try { opt.setAttribute('label', r.label); } catch (e) {}
            try { opt.setAttribute('data-asf-label', r.label); } catch (e) {}
            opt.selected = true;
        }
        ids.push(String(r.id));
    });

    Array.from(sel.options || []).forEach(o => {
        o.selected = ids.indexOf(String(o.value)) >= 0;
    });
    fire(sel);

    function writeChipLabel(chip, lab) {
        if (!chip || !lab) return;
        try { chip.setAttribute('title', lab); } catch (e) {}
        const display = chip.querySelector('.select2-selection__choice__display');
        if (display) {
            display.textContent = lab;
            return;
        }
        const remove = chip.querySelector('.select2-selection__choice__remove');
        if (remove) {
            // Keep × button; replace remaining text nodes
            Array.from(chip.childNodes).forEach(n => {
                if (n !== remove && n.nodeType === 3) n.remove();
                else if (n !== remove && n.nodeType === 1 &&
                    !n.classList.contains('select2-selection__choice__remove')) {
                    try { n.textContent = lab; } catch (e2) {}
                }
            });
            // Ensure free text exists after remove
            let hasText = false;
            Array.from(chip.childNodes).forEach(n => {
                if (n.nodeType === 3 && (n.textContent || '').replace(/×/g, '').trim()) hasText = true;
            });
            if (!hasText && (!display || !display.textContent)) {
                chip.appendChild(document.createTextNode(lab));
            }
        } else {
            chip.textContent = lab;
        }
    }

    function collectChipRoots(selectEl) {
        const roots = [];
        try {
            if ($ && $(selectEl).data('select2')) {
                const s2 = $(selectEl).data('select2');
                if (s2.$selection && s2.$selection[0]) roots.push(s2.$selection[0]);
                if (s2.$container && s2.$container[0]) roots.push(s2.$container[0]);
            }
        } catch (e) {}
        try {
            if (selectEl.nextElementSibling &&
                /select2/.test(selectEl.nextElementSibling.className || '')) {
                roots.push(selectEl.nextElementSibling);
            }
        } catch (e) {}
        const group = selectEl.closest(
            '.control-group, .controls, .object-picker, .cm-object-picker, .cm-field-container, td'
        ) || selectEl.parentElement;
        if (group) roots.push(group);
        return roots;
    }

    function patchCategoryChips(selectEl, list) {
        if (!list || !list.length) return;
        const roots = collectChipRoots(selectEl);
        const used = new Set();
        roots.forEach(root => {
            if (!root || !root.querySelectorAll) return;
            const chips = Array.from(root.querySelectorAll(
                '.select2-selection__choice, li[class*="selection__choice"]'
            ));
            chips.forEach((chip, idx) => {
                const raw = (chip.getAttribute('title') || chip.textContent || '')
                    .replace(/×/g, '').trim();
                let matched = null;
                list.forEach(r => {
                    if (used.has(String(r.id))) return;
                    if (String(raw) === String(r.id) || normalize(raw) === normalize(r.label)) {
                        matched = r;
                    }
                });
                // Broken Select2 chips show "...", id digits, or empty
                if (!matched && (raw === '...' || raw === '…' || /^\d+$/.test(raw) || !raw)) {
                    matched = list[idx] || list.find(r => !used.has(String(r.id)));
                }
                if (matched) {
                    used.add(String(matched.id));
                    writeChipLabel(chip, matched.label);
                }
            });
            // Object-picker plain text selections (non-Select2 multi chips)
            root.querySelectorAll(
                '.object-picker__selection-text, .cm-object-picker-selected, ' +
                '.select2-selection__rendered[title]'
            ).forEach((el, idx) => {
                const raw = (el.getAttribute('title') || el.textContent || '')
                    .replace(/×/g, '').trim();
                if (raw === '...' || raw === '…' || /^\d+$/.test(raw) || !raw) {
                    const r = list[idx] || list[0];
                    if (r) {
                        try { el.setAttribute('title', r.label); } catch (e) {}
                        if (!(el.classList && el.classList.contains('select2-selection__rendered'))) {
                            el.textContent = list.map(x => x.label).join(', ');
                        }
                    }
                }
            });
        });
    }

    try {
        if ($ && $(sel).length) {
            // Ensure each option has real text before Select2 re-renders
            resolved.forEach(r => {
                let $opt = $(sel).find('option').filter(function () {
                    return String(this.value) === String(r.id);
                });
                if (!$opt.length) {
                    const o = new Option(r.label, r.id, true, true);
                    try { o.setAttribute('data-asf-label', r.label); } catch (e) {}
                    $(sel).append(o);
                } else {
                    $opt.each(function () {
                        this.textContent = r.label;
                        try { this.setAttribute('label', r.label); } catch (e) {}
                        try { this.setAttribute('data-asf-label', r.label); } catch (e) {}
                        this.selected = true;
                    });
                }
            });
            // Drop free-standing empty/"..." options that confuse object-picker
            try {
                $(sel).find('option').each(function () {
                    const t = String(this.textContent || '').trim();
                    if ((t === '...' || t === '…') && ids.indexOf(String(this.value)) < 0) {
                        $(this).remove();
                    }
                });
            } catch (e) {}

            if (sel.multiple) {
                $(sel).val(ids).trigger('change');
            } else if (ids.length) {
                $(sel).val(ids[0]).trigger('change');
            }
            try { $(sel).trigger('change.select2'); } catch (e) {}

            // Tell Select2 each selected item has display text
            resolved.forEach(r => {
                try {
                    $(sel).trigger({
                        type: 'select2:select',
                        params: { data: { id: r.id, text: r.label, selected: true } }
                    });
                } catch (e) {}
            });

            // Select2 4 selection refresh with full {id,text} objects
            try {
                const s2 = $(sel).data('select2');
                if (s2 && s2.dataAdapter && typeof s2.trigger === 'function') {
                    const dataObjs = resolved.map(r => ({
                        id: String(r.id),
                        text: r.label,
                        selected: true
                    }));
                    s2.trigger('selection:update', { data: dataObjs });
                }
            } catch (e) {}

            try { if ($(sel).data('select2')) $(sel).select2('close'); } catch (e) {}
            try {
                if (window.Tygh && Tygh.$) {
                    Tygh.$(sel).val(sel.multiple ? ids : ids[0]).trigger('change');
                }
            } catch (e) {}
        }
    } catch (e) {}

    // Always patch visible chips — Select2/object-picker often keeps "..." if AJAX text is missing
    try { patchCategoryChips(sel, resolved); } catch (e) {}
    // Delayed second pass: object-picker re-renders after change handlers
    try {
        setTimeout(function () {
            try {
                // Re-assert option text (page handlers sometimes wipe it)
                resolved.forEach(r => {
                    Array.from(sel.options || []).forEach(o => {
                        if (String(o.value) === String(r.id)) {
                            o.textContent = r.label;
                            o.selected = true;
                        }
                    });
                });
                if ($ && $(sel).length) {
                    try { $(sel).val(sel.multiple ? ids : ids[0]).trigger('change'); } catch (e2) {}
                }
                patchCategoryChips(sel, resolved);
            } catch (e3) {}
        }, 80);
        setTimeout(function () { try { patchCategoryChips(sel, resolved); } catch (e4) {} }, 350);
    } catch (e) {}

    flash(sel);

    let ok = 0;
    if (sel.multiple) {
        const selected = Array.from(sel.selectedOptions || []).map(o => String(o.value));
        ids.forEach(id => { if (selected.indexOf(id) >= 0) ok++; });
    } else if (ids.length && String(sel.value) === String(ids[0])) {
        ok = 1;
    }
    return ok || ids.length;
}

// Stash for Python post-fill chip repair (Select2 re-renders after handlers)
window.__ASF_CAT_FILL_ITEMS = catWantItems.map(it => ({
    id: String(it.id || ''),
    label: String(it.label || '')
})).filter(it => it.id && it.label && it.label !== '...' && it.label !== '…');

// Fill product category select first (correct control)
const productCatSelects = Array.from(document.querySelectorAll('select')).filter(isProductCategorySelectFill);
if (productCatSelects.length && catWantItems.length) {
    productCatSelects.forEach(sel => {
        catMatched += fillProductCategorySelect(sel, catWantItems);
    });
}

// Legacy checkbox trees only if select did nothing
if (catMatched === 0) {
    const catBoxes = Array.from(document.querySelectorAll(
        'input[type="checkbox"][name*="product_data"][name*="category"], ' +
        'input[type="checkbox"][name*="category_ids"]'
    ));
    catBoxes.forEach(box => {
        let lab = '';
        if (box.id) {
            const l = document.querySelector('label[for="' + box.id + '"]');
            if (l) lab = normalize(textOf(l));
        }
        if (!lab) {
            const near = box.closest('label, li, .cm-item, tr');
            if (near) lab = normalize((near.innerText || '').split('\n')[0]);
        }
        const val = String(box.value || '');
        const want = catWantIds.has(val) || catWantLabels.some(w => w && (lab === w || lab.includes(w) || w.includes(lab)));
        if (want) {
            if (!box.checked) { box.click(); fire(box); }
            catMatched += 1;
        }
    });
}

if (catMatched === 0 && catWantLabels.length) {
    catWantLabels.forEach((want) => {
        if (!want) return;
        const candidates = Array.from(document.querySelectorAll('label, a, span, .cm-item, input[type="checkbox"]'));
        for (const el of candidates) {
            const t = normalize(el.innerText || el.textContent || el.value || '');
            if (!t || (t !== want && !t.includes(want))) continue;
            let box = null;
            if (el.tagName === 'INPUT' && el.type === 'checkbox') box = el;
            if (!box && el.getAttribute('for')) box = document.getElementById(el.getAttribute('for'));
            if (!box) {
                const near = el.closest('label, li, tr, .cm-item');
                if (near) box = near.querySelector('input[type="checkbox"]');
            }
            if (box) {
                if (!box.checked) { box.click(); fire(box); }
                catMatched += 1;
                break;
            }
        }
    });
}

results.categories.matched = catMatched;
if (wantedCats.length && catMatched === 0) {
    results.categories.note = 'No category control matched. Re-Scrape on the product page, then Fill again.';
}

// ---------- VIDEOS (AB Video gallery + generic) ----------
function videoLikeFields() {
    return Array.from(document.querySelectorAll('input, textarea, select')).filter(el => {
        const n = (el.getAttribute('name') || '').toLowerCase();
        const id = (el.id || '').toLowerCase();
        const blob = n + ' ' + id;
        const t = (el.type || '').toLowerCase();
        if (t === 'hidden' || t === 'file' || t === 'checkbox' || t === 'radio' || t === 'submit') return false;
        return (
            blob.includes('video') || blob.includes('ab__vg') || blob.includes('ab_vg') ||
            blob.includes('ab__video') || blob.includes('youtube') || blob.includes('vimeo') ||
            blob.includes('video_path') || blob.includes('video_url')
        );
    });
}

const videos = Array.isArray(data.videos) ? data.videos : [];
results.videos.attempted = videos.length;
let vFields = videoLikeFields();
results.videos.fields_on_page = vFields.length;

// Classify fields by role
function roleOf(el) {
    const blob = ((el.getAttribute('name') || '') + ' ' + (el.id || '')).toLowerCase();
    if (blob.includes('title') || blob.includes('name')) return 'title';
    if (blob.includes('desc')) return 'description';
    if (blob.includes('url') || blob.includes('path') || blob.includes('link') || blob.includes('code') || blob.includes('youtube') || blob.includes('iframe')) return 'url';
    if (blob.includes('type') || blob.includes('provider') || blob.includes('host')) return 'type';
    if (blob.includes('pos')) return 'position';
    if (blob.includes('status')) return 'status';
    if (el.tagName === 'TEXTAREA') return 'description';
    if (el.tagName === 'SELECT') return 'type';
    return 'url'; // default guess for first text inputs in video blocks
}

if (videos.length) {
    let vMatched = 0;
    videos.forEach((video, idx) => {
        if (!video || typeof video !== 'object') return;
        const url = String(video.url || '');
        const title = String(video.title || '');
        const desc = String(video.description || '');
        const provider = String(video.provider || 'youtube').toLowerCase();

        vFields = videoLikeFields(); // refresh after add-row click
        const byRole = { url: [], title: [], description: [], type: [], position: [], status: [] };
        vFields.forEach(el => {
            const r = roleOf(el);
            if (byRole[r]) byRole[r].push(el);
        });

        // If no role fields, dump into any empty text input
        if (!byRole.url.length && !byRole.title.length) {
            const textInputs = vFields.filter(el => el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
            if (textInputs[idx] && url) {
                setNativeValue(textInputs[idx], url); fire(textInputs[idx]); flash(textInputs[idx]); vMatched += 1;
            }
        } else {
            const urlEl = byRole.url[idx] || byRole.url.find(e => !e.value) || byRole.url[0];
            if (urlEl && url) { setNativeValue(urlEl, url); fire(urlEl); flash(urlEl); vMatched += 1; }
            const titleEl = byRole.title[idx] || byRole.title.find(e => !e.value) || byRole.title[0];
            if (titleEl && title) { setNativeValue(titleEl, title); fire(titleEl); }
            const descEl = byRole.description[idx] || byRole.description.find(e => !e.value) || byRole.description[0];
            if (descEl && desc) { setNativeValue(descEl, desc); fire(descEl); }
            const typeEl = byRole.type[idx] || byRole.type[0];
            if (typeEl) {
                if (typeEl.tagName === 'SELECT') {
                    const opts = Array.from(typeEl.options);
                    let opt = opts.find(o => normalize(o.textContent).includes(provider) || normalize(o.value).includes(provider));
                    if (!opt) opt = opts.find(o => normalize(o.textContent).includes('youtube') || o.value.toLowerCase().includes('youtube'));
                    if (opt) { typeEl.value = opt.value; fire(typeEl); }
                } else {
                    setNativeValue(typeEl, provider); fire(typeEl);
                }
            }
            if (byRole.position[idx] && video.position !== undefined) {
                setNativeValue(byRole.position[idx], String(video.position)); fire(byRole.position[idx]);
            }
            if (byRole.status[idx]) {
                const st = byRole.status[idx];
                const want = String(video.status || 'A');
                if (st.tagName === 'SELECT') {
                    const opt = Array.from(st.options).find(o => o.value === want || normalize(o.textContent).startsWith('a') || normalize(o.textContent).includes('active'));
                    if (opt) { st.value = opt.value; fire(st); }
                } else {
                    setNativeValue(st, want); fire(st);
                }
            }
        }
    });
    results.videos.matched = vMatched;
    if (vMatched === 0) {
        results.videos.note = vFields.length
            ? 'Video fields found but could not write values — check AB Video gallery field layout.'
            : 'No video fields in DOM. Open tab "AB: Video gallery of the product", click Add video, run again.';
    }
} else {
    results.videos.note = 'No videos in AI payload.';
}

results.debug = {
    productFieldsFound: findAll(['#product_description_product', 'input[name="product_data[product]"]']).length,
    priceFieldsFound: findAll(['#elm_price_price', 'input[name="product_data[price]"]']).length,
    oldPriceFieldsFound: findAll(['#elm_price_list_price', 'input[name="product_data[list_price]"]', 'input[name="product_data[old_price]"]']).length,
    tagFieldsFound: findAll(['input[name*="tags"], select[name*="tags"], .tags input, .cm-tags input']).length,
    descrFieldsFound: findAll([
        'textarea#elm_product_full_descr', 'textarea#elm_full_descr',
        'textarea[name="product_data[full_description]"]'
    ]).length,
    promoFieldsFound: findAll([
        'textarea#elm_product_promo_text',
        'textarea[name="product_data[promo_text]"]'
    ]).length,
    featureFieldsFound: results.features.fields_on_page,
    videoFieldsFound: results.videos.fields_on_page,
    descriptionLength: String(data.full_description || '').length
};

// Explicitly never submit / save.
return results;
"""


def _on_product_update(driver) -> bool:
    try:
        return "dispatch=products.update" in (driver.current_url or "")
    except Exception:
        return False


def _on_login_page(driver) -> bool:
    try:
        url = (driver.current_url or "").lower()
        return (
            "dispatch=auth" in url
            or "dispatch=login" in url
            or "/login" in url
            or "auth.login" in url
        )
    except Exception:
        return False


def _ensure_on_product(driver, product_url: str | None = None) -> None:
    """If a bad click navigated away, return to the product edit URL."""
    if _on_product_update(driver):
        return
    # Never force reload when browser is already on a login screen — user must sign in once
    # in the debug Chrome profile; then they can scrape again.
    if _on_login_page(driver):
        return
    if product_url and "dispatch=products.update" in product_url:
        try:
            driver.get(product_url)
            time.sleep(0.55)
        except Exception:
            pass


def open_product_tabs(driver, product_url: str | None = None) -> dict[str, Any]:
    """Click only in-page product Features / Video tabs (never admin Features menu)."""
    _ensure_on_product(driver, product_url)
    result = driver.execute_script(OPEN_TABS_SCRIPT)
    time.sleep(0.22)
    # Second pass only if Features panel still empty (AJAX panels)
    try:
        need_again = driver.execute_script(
            r"""
            const feats = document.querySelectorAll(
              '#content_features select, #content_product_features select, ' +
              'select[name*="product_features"]'
            ).length;
            return feats < 1;
            """
        )
    except Exception:
        need_again = True
    if need_again:
        result2 = driver.execute_script(OPEN_TABS_SCRIPT)
        time.sleep(0.18)
        if isinstance(result, dict) and isinstance(result2, dict):
            return {
                "clicked": (result.get("clicked") or []) + (result2.get("clicked") or []),
                "tabCount": result2.get("tabCount"),
                "stillOnProduct": _on_product_update(driver),
                "href": driver.current_url,
            }
    return result if isinstance(result, dict) else {}


# ---------------------------------------------------------------------------
# მახასიათებლები / Features — Select2 & object-picker enrichment
# CS-Cart often stores only variant IDs in <option text>; names come via AJAX.
# ---------------------------------------------------------------------------

# Shared JS helper body used by count/open/scrape/ajax (injected inline each time).
_FEATURE_JS_HELPERS = r"""
function asfJq() {
    return window.jQuery || window.$ || (window.Tygh && Tygh.$) || null;
}
function asfFeatureSelects() {
    return Array.from(document.querySelectorAll(
        'select[name*="product_features"], select[name*="feature_data"], ' +
        '#content_features select, #content_product_features select, ' +
        '[id*="content_feature"] select, select.cm-object-picker, ' +
        'select[data-ca-object-picker], select.select2-hidden-accessible'
    )).filter(el => {
        const n = (el.getAttribute('name') || '').toLowerCase();
        const id = (el.id || '').toLowerCase();
        if (n.includes('category') || id.includes('category')) return false;
        if (n.includes('product_features') || n.includes('feature_data') ||
            n.includes('variant') ||
            el.closest('#content_features, #content_product_features, [id*="content_feature"]')) {
            return true;
        }
        return !!el.closest('#content_features, #content_product_features');
    });
}
function asfFeatureId(el) {
    const name = el.getAttribute('name') || '';
    const id = el.id || '';
    let m = name.match(/product_features\]?\[(\d+)\]|features\[(\d+)\]/i);
    if (m) return m[1] || m[2] || '';
    m = (id + ' ' + name).match(/feature[_\[\]-]?(\d{1,6})/i);
    return m ? m[1] : '';
}
function asfIsBadLab(t) {
    const low = String(t || '').toLowerCase();
    if (!t) return true;
    if (low.includes('ჩატვირთვ') || low.includes('loading') || low.includes('searching') ||
        low.includes('no result') || low.includes('მიმდინარე') || low.includes('searching…') ||
        t === '…' || low === 'null' || low === 'undefined') return true;
    return false;
}
function asfCleanLab(t) {
    t = String(t || '').replace(/×/g, '').replace(/\s+/g, ' ').trim();
    if (asfIsBadLab(t)) return '';
    if (t === '-' || t === '—' || t === '–') return '-ცარიელი-';
    // Deduplicate "ავტორი ავტორი"
    const m = t.match(/^(.+?)\s+\1$/);
    if (m) t = m[1].trim();
    return t;
}
/** Prefer real names over pure numeric IDs (first line is often the id). */
function asfBestLabel(raw) {
    const text = String(raw || '');
    if (!text) return '';
    // Nested name nodes are checked by caller; here handle multi-line / spaced blobs
    const parts = text.split(/[\n\r\t|]+/).map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
    const candidates = parts.length ? parts : [text.replace(/\s+/g, ' ').trim()];
    // Also push whole text once as last resort
    if (parts.length > 1) candidates.push(text.replace(/\s+/g, ' ').trim());
    let fallback = '';
    for (const c of candidates) {
        const lab = asfCleanLab(c);
        if (!lab) continue;
        if (lab === '-ცარიელი-') return lab;
        if (!/^\d+$/.test(lab)) return lab;
        if (!fallback) fallback = lab;
    }
    return fallback;
}
function asfIsHuman(lab) {
    if (!lab || lab === '-ცარიელი-') return false;
    if (/^\d+$/.test(lab)) return false;
    // accidental single-letter search residue (never treat as a real variant)
    if (lab.length === 1) return false;
    if (lab === 'ა' || lab === 'a' || lab === 'e' || lab === 's') return false;
    return true;
}
function asfInstallNetHook() {
    if (window.__ASF_NET_HOOKED) return true;
    window.__ASF_NET = [];
    try {
        const XO = XMLHttpRequest.prototype.open;
        const XS = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function (method, url) {
            try { this.__asf_url = String(url || ''); } catch (e) {}
            return XO.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function () {
            try {
                this.addEventListener('load', function () {
                    try {
                        const body = this.responseText || '';
                        if (!body || body.length > 800000) return;
                        const u = String(this.__asf_url || '');
                        // Only keep picker / feature-ish payloads
                        // Keep category picker payloads even if not pure JSON
                        if (u && !/feature|variant|picker|object|select2|ajax|categor|tools\.list|items_list/i.test(u) &&
                            !/^\s*[\[{]/.test(body)) return;
                        window.__ASF_NET.push({ url: u, body: body, t: Date.now() });
                        if (window.__ASF_NET.length > 80) window.__ASF_NET = window.__ASF_NET.slice(-80);
                    } catch (e) {}
                });
            } catch (e) {}
            return XS.apply(this, arguments);
        };
    } catch (e) {}
    try {
        if (window.fetch && !window.__ASF_FETCH_HOOKED) {
            window.__ASF_FETCH_HOOKED = true;
            const ofetch = window.fetch;
            window.fetch = function () {
                const args = arguments;
                return ofetch.apply(this, args).then(function (resp) {
                    try {
                        const clone = resp.clone();
                        clone.text().then(function (body) {
                            if (!body || body.length > 800000) return;
                            let u = '';
                            try { u = String((args[0] && args[0].url) || args[0] || ''); } catch (e) {}
                            window.__ASF_NET.push({ url: u, body: body, t: Date.now() });
                            if (window.__ASF_NET.length > 40) window.__ASF_NET = window.__ASF_NET.slice(-40);
                        });
                    } catch (e) {}
                    return resp;
                });
            };
        }
    } catch (e) {}
    window.__ASF_NET_HOOKED = true;
    return true;
}
function asfParseObjects(payload) {
    const out = [];
    if (payload == null) return out;
    let data = payload;
    if (typeof data === 'string') {
        const t = data.trim();
        if (!t) return out;
        // CS-Cart may wrap JSON in HTML comments / prepend junk
        const brace = t.indexOf('{');
        const brack = t.indexOf('[');
        let start = -1;
        if (brace >= 0 && brack >= 0) start = Math.min(brace, brack);
        else start = Math.max(brace, brack);
        if (start > 0) {
            try { data = JSON.parse(t.slice(start)); } catch (e) { data = t; }
        } else if (t[0] === '{' || t[0] === '[') {
            try { data = JSON.parse(t); } catch (e) { data = t; }
        } else {
            // HTML fallback
            try {
                const doc = new DOMParser().parseFromString(t, 'text/html');
                doc.querySelectorAll('option, li, a, [data-ca-id], [data-id], [data-value]').forEach(node => {
                    let val = node.getAttribute('value') || node.getAttribute('data-ca-id') ||
                        node.getAttribute('data-id') || node.getAttribute('data-value') || '';
                    let lab = asfBestLabel(
                        (node.querySelector('.object-picker__name, .object-picker__selection-text, .select2-result-label, strong, b') || node)
                            .textContent || node.getAttribute('title') || ''
                    );
                    if (!val) {
                        const m = (node.getAttribute('href') || node.getAttribute('id') || '').match(/(\d{2,})/);
                        if (m) val = m[1];
                    }
                    if (lab && !/^\d+$/.test(lab)) out.push({ value: String(val || ''), label: lab });
                });
            } catch (e) {}
            return out;
        }
    }
    if (!data || typeof data !== 'object') return out;

    let arr = null;
    if (Array.isArray(data)) arr = data;
    else {
        arr = data.objects || data.results || data.variants || data.items || data.data || null;
        // map/object keyed by id
        if (!arr && data.variants && typeof data.variants === 'object' && !Array.isArray(data.variants)) {
            arr = Object.keys(data.variants).map(k => {
                const v = data.variants[k] || {};
                return Object.assign({ id: k }, v);
            });
        }
        if (!arr && typeof data === 'object') {
            // Sometimes response itself is { "712": {variant: "Name"}, ... }
            const keys = Object.keys(data);
            if (keys.length && keys.every(k => /^\d+$/.test(k))) {
                arr = keys.map(k => Object.assign({ id: k }, data[k] || {}));
            }
        }
    }
    if (!Array.isArray(arr)) return out;
    arr.forEach(row => {
        if (!row) return;
        if (typeof row === 'string' || typeof row === 'number') return;
        const val = String(
            row.id != null ? row.id :
            (row.variant_id != null ? row.variant_id :
            (row.value != null ? row.value :
            (row.object_id != null ? row.object_id : '')))
        );
        let lab = asfBestLabel(
            row.text || row.name || row.variant || row.label || row.title ||
            row.variant_name || row.description || ''
        );
        // Nested content
        if (!asfIsHuman(lab) && row.data) {
            lab = asfBestLabel(row.data.text || row.data.name || row.data.variant || '');
        }
        if (!val && !lab) return;
        if (asfIsHuman(lab)) out.push({ value: val, label: lab });
        else if (val && lab && lab === '-ცარიელი-') out.push({ value: '', label: lab });
    });
    return out;
}
function asfAbsorbIntoSelect(el, pairs) {
    let n = 0;
    (pairs || []).forEach(it => {
        let val = String(it.value == null ? '' : it.value);
        let lab = asfBestLabel(it.label);
        if (!asfIsHuman(lab) && lab !== '-ცარიელი-') return;
        if (lab === '-ცარიელი-') val = '';
        let opt = Array.from(el.options || []).find(o => String(o.value) === String(val));
        if (!opt) {
            opt = document.createElement('option');
            opt.value = val;
            el.appendChild(opt);
        }
        opt.textContent = lab;
        try { opt.setAttribute('data-asf-label', lab); } catch (e) {}
        n++;
    });
    return n;
}
function asfDiscoverAjaxUrls(el) {
    const $ = asfJq();
    const urls = [];
    const push = (u) => {
        if (!u) return;
        const s = String(u);
        if (s && urls.indexOf(s) < 0) urls.push(s);
    };
    [
        'data-ca-data-url', 'data-ca-result-url', 'data-ca-load-url', 'data-ca-ajax-url',
        'data-ca-picker-url', 'data-url', 'data-ajax-url', 'data-ca-object-picker-ajax-url'
    ].forEach(a => push(el.getAttribute(a)));
    try {
        if ($) {
            const d = $(el).data() || {};
            ['caDataUrl', 'caResultUrl', 'caLoadUrl', 'ajaxUrl', 'url', 'caPickerUrl',
             'caObjectPickerAjaxUrl', 'caAjaxUrl'].forEach(k => push(d[k]));
        }
    } catch (e) {}
    try {
        if ($ && $(el).data('select2')) {
            const s2 = $(el).data('select2');
            const ajax = s2.options && s2.options.get ? s2.options.get('ajax') : (s2.options && s2.options.ajax);
            if (ajax && ajax.url) {
                try { push(typeof ajax.url === 'function' ? ajax.url({ term: '' }) : ajax.url); } catch (e) {}
            }
        }
    } catch (e) {}
    // Parent object-picker container can carry the URL
    try {
        const root = el.closest('[data-ca-data-url], [data-ca-object-picker], .object-picker, .cm-object-picker');
        if (root && root !== el) {
            [
                'data-ca-data-url', 'data-ca-result-url', 'data-ca-load-url', 'data-ca-ajax-url'
            ].forEach(a => push(root.getAttribute(a)));
        }
    } catch (e) {}
    const featureId = asfFeatureId(el);
    if (featureId) {
        const base = location.href.split('?')[0];
        // Full list requests only — empty search, large page size, several pages
        const dispatches = [
            'product_features.get_variants_list',
            'product_features.variants_list'
        ];
        dispatches.forEach(disp => {
            for (let page = 1; page <= 8; page++) {
                push(
                    base + '?dispatch=' + encodeURIComponent(disp) +
                    '&feature_id=' + encodeURIComponent(featureId) +
                    '&page=' + page +
                    '&page_size=100&items_per_page=100&q=&search_query=&pattern='
                );
            }
        });
        push(base + '?dispatch=product_features.get_variants_list&feature_id=' + featureId +
            '&page_size=500&items_per_page=500&q=');
    }
    return urls;
}
function asfApplyPairsCache(el, pairs, index) {
    window.__ASF_FEATURE_OPTIONS = window.__ASF_FEATURE_OPTIONS || {};
    const key = el.getAttribute('name') || el.id || ('idx_' + index);
    const byVal = {};
    (window.__ASF_FEATURE_OPTIONS[key] || []).forEach(p => { byVal[String(p.value)] = p; });
    (pairs || []).forEach(it => {
        const v = String(it.value == null ? '' : it.value);
        const lab = asfBestLabel(it.label);
        if (!lab) return;
        if (!byVal[v] || (asfIsHuman(lab) && !asfIsHuman(byVal[v].label))) {
            byVal[v] = { value: v, label: lab };
        }
    });
    // Prefer non-numeric list for app
    window.__ASF_FEATURE_OPTIONS[key] = Object.values(byVal);
    return window.__ASF_FEATURE_OPTIONS[key];
}
"""

ENRICH_SELECT2_COUNT_SCRIPT = _FEATURE_JS_HELPERS + r"""
return asfFeatureSelects().length;
"""

ENRICH_FEATURE_HOOK_SCRIPT = _FEATURE_JS_HELPERS + r"""
return { ok: asfInstallNetHook() };
"""

ENRICH_FEATURE_OPEN_SCRIPT = _FEATURE_JS_HELPERS + r"""
const index = arguments[0];
asfInstallNetHook();
window.__ASF_NET_MARK = Date.now();

const $ = asfJq();
const selects = asfFeatureSelects();
const el = selects[index];
if (!el) return { ok: false, reason: 'missing' };

// Reveal features tab content
['content_features', 'content_product_features'].forEach(id => {
    const n = document.getElementById(id);
    if (n) { n.classList.remove('hidden', 'collapsed'); n.style.display = ''; }
});

// Close other select2s
try {
    if ($ && $.fn && $.fn.select2) {
        selects.forEach(s => { try { if ($(s).data('select2')) $(s).select2('close'); } catch (e) {} });
    }
} catch (e) {}
try { document.body.click(); } catch (e) {}

let opened = false;
try {
    if ($ && $(el).data('select2')) { $(el).select2('open'); opened = true; }
    else if ($ && $.fn && $.fn.select2) { try { $(el).select2('open'); opened = true; } catch (e) {} }
} catch (e) {}

if (!opened) {
    const root = el.closest(
        '.control-group, .ty-control-group, .cm-field-container, .object-picker, .cm-object-picker, td, .controls'
    ) || el.parentElement;
    const clickables = root ? root.querySelectorAll(
        '.select2-selection, .select2-choice, .select2-selection__rendered, ' +
        '.cm-object-picker-header, .object-picker__selection, .object-picker__select, ' +
        '[class*="select2-selection"]'
    ) : [];
    for (const c of clickables) {
        try { c.click(); opened = true; break; } catch (e) {}
    }
}
if (!opened) {
    try {
        el.focus();
        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
        el.click();
        opened = true;
    } catch (e) {}
}

// Trigger Select2 remote query with EMPTY term only (never type search text —
// typing can create new categories/variants named e.g. "ა" if the form is saved).
try {
    if ($ && $(el).data('select2')) {
        const s2 = $(el).data('select2');
        try { s2.trigger('query', { term: '' }); } catch (e) {}
        try {
            if (s2.dataAdapter && s2.dataAdapter.query) {
                s2.dataAdapter.query({ term: '' }, function () {});
            }
        } catch (e) {}
    }
} catch (e) {}

// Focus search textbox only — do not set value / dispatch keystrokes
try {
    const search = document.querySelector(
        '.select2-container--open .select2-search__field, .select2-dropdown .select2-search__field, ' +
        '.select2-search__field, .object-picker__query, input.select2-input'
    );
    if (search) {
        search.focus();
        // Clear any leftover typed query so picker shows the full default list
        if (search.value) {
            search.value = '';
        }
    }
} catch (e) {}

const urls = asfDiscoverAjaxUrls(el);
return {
    ok: true,
    opened: opened,
    index: index,
    name: el.getAttribute('name') || '',
    id: el.id || '',
    featureId: asfFeatureId(el),
    ajaxUrl: urls[0] || '',
    ajaxUrls: urls.slice(0, 6),
    optionCount: el.options ? el.options.length : 0
};
"""

ENRICH_FEATURE_SCRAPE_SCRIPT = _FEATURE_JS_HELPERS + r"""
const index = arguments[0];
const $ = asfJq();
const el = asfFeatureSelects()[index];
if (!el) return { ok: false, count: 0, items: [], human: 0 };

const pairs = [];
const seen = new Set();
function push(val, lab) {
    val = String(val == null ? '' : val).trim();
    lab = asfBestLabel(lab);
    if (!lab) return;
    // If label is numeric, use it as value and keep (last resort)
    if (!val && /^\d+$/.test(lab)) val = lab;
    if (!val && lab === '-ცარიელი-') val = '';
    // Extract id from select2 result element ids
    if (!val || (!/^\d+$/.test(val) && val.length > 24)) {
        const m = String(val).match(/(\d{2,})$/);
        if (m) val = m[1];
    }
    const key = val + '||' + lab;
    if (seen.has(key)) return;
    seen.add(key);
    pairs.push({ value: val, label: lab });
}

// 1) Live dropdown results — prefer nested name / select2 data
document.querySelectorAll(
    '.select2-results__option, .select2-result-label, li.select2-result, ' +
    '.select2-results li, .object-picker__result, .cm-object-picker-result, ' +
    '[class*="object-picker"] li, .select2-result-selectable, ' +
    '.select2-dropdown li, .tt-suggestion'
).forEach(node => {
    if (node.classList.contains('loading-results') ||
        node.classList.contains('select2-results__option--load-more') ||
        node.classList.contains('select2-results__message') ||
        node.getAttribute('data-select2-tag') === 'true') return;
    let val = node.getAttribute('data-ca-id') || node.getAttribute('data-id') ||
        node.getAttribute('data-value') || '';
    // select2 data object (id + text with real name)
    try {
        if ($ && $(node).data('data')) {
            const d = $(node).data('data');
            if (d) {
                if (d.newTag || d.loading) return;
                if (d.id != null) val = String(d.id);
                const t = d.text || d.name || d.label || (d.data && (d.data.name || d.data.text));
                if (asfIsHuman(asfBestLabel(t))) {
                    push(val, t);
                    return;
                }
            }
        }
    } catch (e) {}
    const nameNode = node.querySelector(
        '.object-picker__name, .object-picker__selection-text, .select2-result-label, ' +
        '.select2-selection__choice__display, .object-picker__result-title, strong, b, .title, .name'
    );
    let lab = '';
    if (nameNode) lab = asfBestLabel(nameNode.textContent || nameNode.getAttribute('title') || '');
    if (!asfIsHuman(lab)) lab = asfBestLabel(node.getAttribute('title') || '');
    if (!asfIsHuman(lab)) lab = asfBestLabel(node.innerText || node.textContent || '');
    if (lab === 'ა' || lab === 'a' || lab.length === 1) return;
    if (/create|ახალ|დამატ|add new/i.test(lab)) return;
    const idStr = String(node.getAttribute('id') || val || '');
    const m = idStr.match(/result-[^-]+-(\d+)/) || idStr.match(/-(\d+)$/);
    if (m) val = m[1];
    if (lab) push(val, lab);
});

// 2) Select2 selected / current dataset
try {
    if ($ && $(el).data('select2')) {
        try {
            const data = $(el).select2('data');
            const arr = Array.isArray(data) ? data : (data ? [data] : []);
            arr.forEach(d => {
                if (!d) return;
                push(d.id, d.text || d.name || d.label || '');
            });
        } catch (e) {}
        try {
            const s2 = $(el).data('select2');
            if (s2.$results) {
                s2.$results.find('.select2-results__option').each(function () {
                    const d = $(this).data('data');
                    if (d) push(d.id, d.text || d.name || '');
                });
            }
        } catch (e) {}
    }
} catch (e) {}

// 3) Rendered selection chips (current value name)
try {
    const wrap = el.closest('.control-group, .controls, .cm-field-container, .object-picker, td') || el.parentElement;
    if (wrap) {
        wrap.querySelectorAll(
            '.select2-selection__rendered, .select2-chosen, .select2-selection__choice, ' +
            '.object-picker__selection-text, .cm-object-picker-selected, .select2-selection__choice__display'
        ).forEach(r => {
            const lab = asfBestLabel((r.getAttribute('title') || r.innerText || '').replace(/×/g, ''));
            if (asfIsHuman(lab)) push(el.value || '', lab);
        });
    }
} catch (e) {}

// 4) Network captures since open
const mark = window.__ASF_NET_MARK || 0;
const featureId = asfFeatureId(el);
(window.__ASF_NET || []).forEach(entry => {
    if (!entry || !entry.body) return;
    if (mark && entry.t && entry.t < mark - 50) return;
    // If entry URL mentions another feature_id, skip
    if (featureId && entry.url && /feature_id=(\d+)/i.test(entry.url)) {
        const m = entry.url.match(/feature_id=(\d+)/i);
        if (m && m[1] !== String(featureId)) return;
    }
    asfParseObjects(entry.body).forEach(p => push(p.value, p.label));
});

// 5) Existing options — only keep non-numeric text (or empty)
Array.from(el.options || []).forEach(o => {
    const val = String(o.value || '');
    const lab = asfBestLabel(
        o.getAttribute('data-asf-label') || o.getAttribute('data-ca-name') ||
        o.getAttribute('title') || o.textContent || ''
    );
    // Skip pure numeric options at this stage if we already have human names
    if (lab) push(val, lab);
});

// Write human labels into DOM; do NOT overwrite good names with numbers
let updated = 0;
let human = 0;
pairs.forEach(it => {
    if (asfIsHuman(it.label)) human++;
    if (!asfIsHuman(it.label) && it.label !== '-ცარიელი-') return;
    updated += asfAbsorbIntoSelect(el, [it]);
});

// Cache: prefer human pairs; if we only have numbers, still cache so UI is complete
const cachePairs = pairs.filter(p => asfIsHuman(p.label) || p.label === '-ცარიელი-');
const finalPairs = cachePairs.length ? cachePairs : pairs;
asfApplyPairsCache(el, finalPairs, index);

return {
    ok: true,
    count: pairs.length,
    human: human,
    updated: updated,
    name: el.getAttribute('name') || '',
    featureId: featureId,
    sample: finalPairs.filter(p => asfIsHuman(p.label)).slice(0, 8),
    allNumeric: pairs.length > 0 && human === 0
};
"""

ENRICH_FEATURE_AJAX_SCRIPT = _FEATURE_JS_HELPERS + r"""
const index = arguments[0];
const el = asfFeatureSelects()[index];
if (!el) return { ok: false, reason: 'no el', loaded: 0 };

asfInstallNetHook();
const featureId = asfFeatureId(el);
const urls = asfDiscoverAjaxUrls(el);
let loaded = 0;
const pairs = [];

function absorb(body) {
    const got = asfParseObjects(body);
    got.forEach(p => {
        if (!asfIsHuman(p.label) && p.label !== '-ცარიელი-') return;
        pairs.push(p);
        loaded += asfAbsorbIntoSelect(el, [p]);
    });
}

// 1) Direct GET of discovered picker URLs (empty query only — never q=<typed text>)
for (const u of urls.slice(0, 24)) {
    try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', u, false);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');
        xhr.send(null);
        if (xhr.status >= 200 && xhr.status < 400 && xhr.responseText) {
            absorb(xhr.responseText);
        }
    } catch (e) {}
}

// 1b) CS-Cart $.ceAjax when available (handles security tokens / session better)
try {
    const $ = asfJq();
    if ($ && $.ceAjax && featureId) {
        const base = location.href.split('?')[0];
        for (let page = 1; page <= 6; page++) {
            const u = base + '?dispatch=product_features.get_variants_list&feature_id=' +
                featureId + '&page=' + page + '&page_size=100&items_per_page=100&q=';
            try {
                let body = null;
                $.ceAjax('request', u, {
                    method: 'get',
                    caching: false,
                    hidden: true,
                    async: false,
                    callback: function (data) {
                        try {
                            if (typeof data === 'string') body = data;
                            else body = JSON.stringify(data);
                        } catch (e) {}
                    }
                });
                if (body) absorb(body);
            } catch (e) {}
        }
    }
} catch (e) {}

// 2) jQuery ajax when available (same cookies/session)
try {
    const $ = asfJq();
    if ($ && loaded < 5) {
        for (const u of urls.slice(0, 12)) {
            try {
                $.ajax({
                    url: u,
                    async: false,
                    dataType: 'text',
                    cache: false,
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    success: function (body) { absorb(body); }
                });
            } catch (e) {}
        }
    }
} catch (e) {}

// 3) Recent network buffer (responses from open + empty query)
(window.__ASF_NET || []).slice(-20).forEach(entry => {
    if (!entry || !entry.body) return;
    if (featureId && entry.url && /feature_id=(\d+)/i.test(entry.url)) {
        const m = entry.url.match(/feature_id=(\d+)/i);
        if (m && m[1] !== String(featureId)) return;
    }
    // Skip responses that look like a typed search query (q=single non-empty char)
    if (entry.url && /[?&](q|search_query|pattern)=[^&]+/.test(entry.url)) {
        const m = entry.url.match(/[?&](?:q|search_query|pattern)=([^&]*)/i);
        if (m && decodeURIComponent(m[1] || '').trim().length > 0) return;
    }
    absorb(entry.body);
});

asfApplyPairsCache(el, pairs, index);
const human = pairs.filter(p => asfIsHuman(p.label)).length;
return {
    ok: true,
    featureId: featureId,
    urlsTried: urls.length,
    loaded: loaded,
    human: human,
    sample: pairs.filter(p => asfIsHuman(p.label)).slice(0, 8),
    options: el.options ? el.options.length : 0
};
"""

ENRICH_FEATURE_CLOSE_SCRIPT = r"""
try {
  const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$);
  if ($ && $.fn && $.fn.select2) {
    $('select').each(function () {
      try { if ($(this).data('select2')) $(this).select2('close'); } catch (e) {}
    });
  }
} catch (e) {}
try { document.body.click(); } catch (e) {}
return true;
"""

# Apply persisted feature option lists (brand, condition, etc.) without AJAX.
INJECT_FEATURE_CACHE_SCRIPT = _FEATURE_JS_HELPERS + r"""
const map = arguments[0] || {};
asfInstallNetHook();
window.__ASF_FEATURE_OPTIONS = window.__ASF_FEATURE_OPTIONS || {};
let applied = 0;
const report = [];
asfFeatureSelects().forEach((el, index) => {
    const fid = asfFeatureId(el);
    const name = el.getAttribute('name') || '';
    let opts = null;
    if (fid && map[fid]) opts = map[fid];
    if (!opts && name && map[name]) opts = map[name];
    if (!opts && fid) {
        // partial key match
        Object.keys(map).forEach(k => {
            if (!opts && (k === fid || String(k).indexOf(fid) >= 0 || String(fid).indexOf(k) >= 0))
                opts = map[k];
        });
    }
    if (!Array.isArray(opts) || !opts.length) return;
    const pairs = opts.map(o => ({
        value: String((o && o.value) != null ? o.value : ''),
        label: String((o && o.label) || '')
    })).filter(p => p.label && !/^\d+$/.test(p.label));
    if (!pairs.length) return;
    asfAbsorbIntoSelect(el, pairs);
    asfApplyPairsCache(el, pairs, index);
    if (fid) {
        window.__ASF_FEATURE_OPTIONS[fid] = pairs;
    }
    if (name) window.__ASF_FEATURE_OPTIONS[name] = pairs;
    applied += 1;
    report.push({ featureId: fid, count: pairs.length });
});
return { applied: applied, features: report };
"""

# One round-trip: AJAX-load ALL feature variant lists (skip ones already rich).
ENRICH_ALL_FEATURES_BATCH_SCRIPT = _FEATURE_JS_HELPERS + r"""
const maxFeatures = Math.min(Number(arguments[0] || 40), 40);
asfInstallNetHook();
const selects = asfFeatureSelects().slice(0, maxFeatures);
const results = [];
let totalLoaded = 0;
let totalHuman = 0;

function humanOnSelect(el) {
    let n = 0;
    Array.from(el.options || []).forEach(o => {
        const lab = asfCleanLab(o.textContent || o.label || o.getAttribute('data-asf-label') || '');
        if (asfIsHuman(lab)) n += 1;
    });
    return n;
}

selects.forEach((el, index) => {
    const featureId = asfFeatureId(el);
    const already = humanOnSelect(el);
    // Already rich (from disk cache inject or prior fill) — skip network
    if (already >= 8) {
        results.push({ index: index, featureId: featureId, skipped: true, human: already, loaded: 0 });
        totalHuman += already;
        return;
    }

    const urls = asfDiscoverAjaxUrls(el).slice(0, 8);
    let loaded = 0;
    const pairs = [];
    function absorb(body) {
        const got = asfParseObjects(body);
        got.forEach(p => {
            if (!asfIsHuman(p.label) && p.label !== '-ცარიელი-') return;
            pairs.push(p);
            loaded += asfAbsorbIntoSelect(el, [p]);
        });
    }
    for (const u of urls) {
        try {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', u, false);
            xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            xhr.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');
            xhr.send(null);
            if (xhr.status >= 200 && xhr.status < 400 && xhr.responseText) absorb(xhr.responseText);
            if (pairs.filter(p => asfIsHuman(p.label)).length >= 30) break;
        } catch (e) {}
    }
    // CS-Cart variants list for this feature (paginated, limited pages)
    try {
        const $ = asfJq();
        if ($ && $.ceAjax && featureId) {
            const base = location.href.split('?')[0];
            for (let page = 1; page <= 3; page++) {
                const u = base + '?dispatch=product_features.get_variants_list&feature_id=' +
                    featureId + '&page=' + page + '&page_size=100&items_per_page=100&q=';
                try {
                    let body = null;
                    $.ceAjax('request', u, {
                        method: 'get', caching: false, hidden: true, async: false,
                        callback: function (data) {
                            try {
                                body = (typeof data === 'string') ? data : JSON.stringify(data);
                            } catch (e) {}
                        }
                    });
                    if (body) absorb(body);
                } catch (e) {}
                if (pairs.filter(p => asfIsHuman(p.label)).length >= 40) break;
            }
        }
    } catch (e) {}

    asfApplyPairsCache(el, pairs, index);
    const human = pairs.filter(p => asfIsHuman(p.label)).length + already;
    totalLoaded += loaded;
    totalHuman += human;
    results.push({
        index: index,
        featureId: featureId,
        skipped: false,
        loaded: loaded,
        human: human,
        sample: pairs.filter(p => asfIsHuman(p.label)).slice(0, 4)
    });
});

// Export map for Python disk cache: featureId -> options
const exportMap = {};
Object.keys(window.__ASF_FEATURE_OPTIONS || {}).forEach(k => {
    const arr = window.__ASF_FEATURE_OPTIONS[k];
    if (!Array.isArray(arr) || !arr.length) return;
    const humans = arr.filter(p => p && asfIsHuman(p.label));
    if (humans.length >= 2) exportMap[k] = humans;
});

return {
    ok: true,
    count: selects.length,
    totalLoaded: totalLoaded,
    totalHuman: totalHuman,
    results: results,
    exportMap: exportMap
};
"""


def enrich_feature_select2_options(driver, product_url: str | None = None) -> dict[str, Any]:
    """
    Load real feature option names for AI matching.

    Strategy (fast + accurate):
    1) Inject last session/disk cache of brand/variant lists (shop-wide, shared)
    2) One batch AJAX pass for every feature still thin
    3) Open only remaining skinny dropdowns (never type into search)
    4) Persist improved lists to disk for next product
    """
    _ensure_on_product(driver, product_url)
    try:
        driver.execute_script(OPEN_TABS_SCRIPT)
        time.sleep(0.15)
    except Exception:
        pass
    _ensure_on_product(driver, product_url)

    try:
        driver.execute_script(ENRICH_FEATURE_HOOK_SCRIPT)
    except Exception:
        pass

    admin = _admin_script_url(driver)
    disk_map = _disk_load_features(admin)
    injected = 0
    if disk_map:
        try:
            info = driver.execute_script(INJECT_FEATURE_CACHE_SCRIPT, disk_map) or {}
            injected = int((info or {}).get("applied") or 0)
        except Exception:
            injected = 0

    # Batch AJAX all features in a single Selenium round-trip
    batch: dict[str, Any] = {}
    try:
        batch = driver.execute_script(ENRICH_ALL_FEATURES_BATCH_SCRIPT, 40) or {}
    except Exception as exc:
        batch = {"error": str(exc), "results": []}

    total_items = int(batch.get("totalLoaded") or 0)
    human_total = int(batch.get("totalHuman") or 0)
    samples: list[dict] = []
    for r in batch.get("results") or []:
        if isinstance(r, dict) and r.get("sample"):
            samples.append(
                {"name": r.get("featureId"), "sample": r.get("sample"), "via": "batch"}
            )

    export_map = batch.get("exportMap") if isinstance(batch.get("exportMap"), dict) else {}
    if export_map:
        try:
            _disk_save_features(admin, export_map)
        except Exception:
            pass

    # How many selects still need a real open?
    try:
        thin = driver.execute_script(
            _FEATURE_JS_HELPERS
            + r"""
            const out = [];
            asfFeatureSelects().forEach((el, i) => {
                let human = 0;
                Array.from(el.options || []).forEach(o => {
                    const lab = asfCleanLab(o.textContent || o.label || '');
                    if (asfIsHuman(lab)) human += 1;
                });
                if (human < 5) out.push(i);
            });
            return out;
            """
        ) or []
    except Exception:
        thin = []

    if not isinstance(thin, list):
        thin = []
    thin = [int(x) for x in thin if str(x).isdigit()][:12]  # cap expensive opens

    focus_scroll = r"""
        const search = document.querySelector(
          '.select2-container--open .select2-search__field, ' +
          '.select2-dropdown .select2-search__field, .select2-search__field, ' +
          '.object-picker__query, input.select2-input'
        );
        if (search) {
          try { search.focus(); if (search.value) search.value = ''; } catch (e) {}
        }
        document.querySelectorAll(
          '.select2-results__options, .select2-results, .object-picker__results'
        ).forEach(b => {
          try { b.scrollTop = Math.min(b.scrollTop + 500, b.scrollHeight); } catch (e) {}
        });
        return true;
    """

    opened = 0
    for i in thin:
        try:
            opened += 1
            driver.execute_script(ENRICH_FEATURE_OPEN_SCRIPT, i)
            last_c = -1
            for attempt in range(3):
                time.sleep(0.12 if attempt else 0.16)
                try:
                    driver.execute_script(focus_scroll)
                except Exception:
                    pass
                try:
                    res_try = driver.execute_script(ENRICH_FEATURE_SCRAPE_SCRIPT, i) or {}
                    c = int(res_try.get("count") or 0)
                    h = int(res_try.get("human") or 0)
                    if h >= 8 or (c == last_c and c >= 3):
                        break
                    last_c = c
                except Exception:
                    pass
            res = driver.execute_script(ENRICH_FEATURE_SCRAPE_SCRIPT, i) or {}
            total_items += int(res.get("count") or 0)
            human_total += int(res.get("human") or 0)
            if int(res.get("human") or 0) < 4:
                try:
                    aj = driver.execute_script(ENRICH_FEATURE_AJAX_SCRIPT, i) or {}
                    human_total += int(aj.get("human") or 0)
                    total_items += int(aj.get("loaded") or 0)
                except Exception:
                    pass
            try:
                driver.execute_script(ENRICH_FEATURE_CLOSE_SCRIPT)
            except Exception:
                pass
        except Exception:
            try:
                driver.execute_script(ENRICH_FEATURE_CLOSE_SCRIPT)
            except Exception:
                pass

    # Snapshot feature options for disk after open/scrape pass
    try:
        final_map = driver.execute_script(
            _FEATURE_JS_HELPERS
            + r"""
            const exportMap = {};
            const cache = window.__ASF_FEATURE_OPTIONS || {};
            Object.keys(cache).forEach(k => {
                const arr = cache[k];
                if (!Array.isArray(arr)) return;
                const humans = arr.filter(p => p && asfIsHuman(p.label));
                if (humans.length >= 2) exportMap[k] = humans.map(p => ({
                    value: String(p.value || ''), label: String(p.label || '')
                }));
            });
            // also from selects
            asfFeatureSelects().forEach(el => {
                const fid = asfFeatureId(el);
                if (!fid) return;
                const pairs = [];
                Array.from(el.options || []).forEach(o => {
                    const lab = asfCleanLab(o.getAttribute('data-asf-label') || o.textContent || o.label || '');
                    if (asfIsHuman(lab)) pairs.push({ value: String(o.value || ''), label: lab });
                });
                if (pairs.length >= 2) {
                    const prev = exportMap[fid] || [];
                    const by = {};
                    prev.concat(pairs).forEach(p => { by[p.value || p.label] = p; });
                    exportMap[fid] = Object.values(by);
                }
            });
            return exportMap;
            """
        ) or {}
        if isinstance(final_map, dict) and final_map:
            _disk_save_features(admin, final_map)
    except Exception:
        pass

    return {
        "selects": int(batch.get("count") or 0),
        "cache_injected": injected,
        "opened_dropdowns": opened,
        "options_seen": total_items,
        "human_labels": human_total,
        "ajax_fixes": int(batch.get("count") or 0) - len([r for r in (batch.get("results") or []) if isinstance(r, dict) and r.get("skipped")]),
        "batch": True,
        "samples": samples[:10],
    }

# Open product Categories object-picker (textbox under კატეგორიები) — NOT admin menus
OPEN_CATEGORY_PICKER_SCRIPT = r"""
const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$) || null;

function isJunkLabel(t) {
    t = String(t || '').toLowerCase();
    return /storefront|alexbranding|cart-power|add-on|addon|cs-cart georgia|გადახდ|ტრანსპორტ|market/i.test(t);
}

function productCategorySelects() {
    return Array.from(document.querySelectorAll('select')).filter(el => {
        const n = (el.getAttribute('name') || '').toLowerCase();
        const id = (el.id || '').toLowerCase();
        if (n.includes('product_data') && n.includes('category')) return true;
        if (n.includes('category_ids') && !n.includes('company') && !n.includes('storefront')) return true;
        if (id.includes('product_categor')) return true;
        // Label "კატეგორიები" on product General tab
        const group = el.closest('.control-group, .ty-control-group, .form-group, tr');
        if (!group) return false;
        const lab = group.querySelector('label, .control-label');
        const t = ((lab && (lab.innerText || lab.textContent)) || '').replace(/\s+/g, ' ').trim().toLowerCase();
        if (!(t.includes('კატეგორ') || (t.includes('categor') && !t.includes('feature')))) return false;
        if (isJunkLabel(t)) return false;
        // Prefer object-picker / select2 fields in that group
        return true;
    });
}

// Prefer group found by exact product label + breadcrumbs help text
let pickerRoot = null;
let selectEl = null;
document.querySelectorAll('label, .control-label').forEach(lab => {
    const t = ((lab.innerText || lab.textContent) || '').replace(/\s+/g, ' ').trim().toLowerCase();
    if (!(t.includes('კატეგორ') || t === 'categories' || t.startsWith('categories'))) return;
    if (t.includes('storefront') || t.includes('feature')) return;
    const g = lab.closest('.control-group, .ty-control-group, .form-group, tr') || lab.parentElement;
    if (!g) return;
    // Prefer group that mentions breadcrumbs (product categories help text)
    const help = ((g.innerText || '') + '').toLowerCase();
    const score = (help.includes('breadcrumb') || help.includes('ნავიგაც') ? 10 : 0) +
        (g.querySelector('select[name*="product_data"]') ? 5 : 0) +
        (g.querySelector('select[name*="category_ids"]') ? 4 : 0) +
        (g.querySelector('.cm-object-picker, .object-picker, .select2-container') ? 3 : 0);
    if (!pickerRoot || score > (pickerRoot.__asfScore || 0)) {
        pickerRoot = g;
        pickerRoot.__asfScore = score;
    }
});

if (pickerRoot) {
    selectEl = pickerRoot.querySelector(
        'select[name*="product_data"][name*="category"], ' +
        'select[name*="category_ids"], select.cm-object-picker, ' +
        'select.select2-hidden-accessible, select'
    );
}

if (!selectEl) {
    const list = productCategorySelects();
    selectEl = list[0] || null;
    if (selectEl) {
        pickerRoot = selectEl.closest('.control-group, .ty-control-group, .form-group, tr, .controls') ||
            selectEl.parentElement;
    }
}

// Close any open pickers first
try {
    if ($ && $.fn && $.fn.select2) {
        $('select').each(function () {
            try { if ($(this).data('select2')) $(this).select2('close'); } catch (e) {}
        });
    }
} catch (e) {}
try { document.body.click(); } catch (e) {}

let opened = false;
let clickTarget = '';

if (selectEl) {
    window.__ASF_CATEGORY_SELECT = selectEl;
    window.__ASF_CATEGORY_FIELD_NAME = selectEl.getAttribute('name') || '';
    // Click the visible textbox / selection UI next to the select (CS-Cart object-picker)
    const root = pickerRoot || selectEl.parentElement;
    const clickables = root ? root.querySelectorAll(
        '.select2-selection, .select2-selection--multiple, .select2-selection--single, ' +
        '.select2-choice, .select2-search--inline, .select2-search__field, ' +
        '.object-picker__selection, .cm-object-picker-header, .object-picker__select-container, ' +
        '.object-picker, .cm-object-picker, input.select2-search__field'
    ) : [];
    try {
        if ($ && $(selectEl).data('select2')) {
            $(selectEl).select2('open');
            opened = true;
            clickTarget = 'select2_open';
        }
    } catch (e) {}
    if (!opened) {
        for (const c of clickables) {
            try {
                c.click();
                c.focus();
                opened = true;
                clickTarget = c.className || c.tagName;
                break;
            } catch (e) {}
        }
    }
    if (!opened) {
        try {
            selectEl.focus();
            selectEl.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            selectEl.click();
            opened = true;
            clickTarget = 'select_click';
        } catch (e) {}
    }
    // Fire empty query so full list loads (never type characters)
    try {
        if ($ && $(selectEl).data('select2')) {
            const s2 = $(selectEl).data('select2');
            try { s2.trigger('query', { term: '' }); } catch (e) {}
            try {
                if (s2.dataAdapter && s2.dataAdapter.query) {
                    s2.dataAdapter.query({ term: '' }, function () {});
                }
            } catch (e) {}
        }
    } catch (e) {}
}

// Focus search textbox inside open dropdown only (clear only — do not type)
try {
    const search = document.querySelector(
        '.select2-container--open .select2-search__field, ' +
        '.select2-dropdown .select2-search__field, ' +
        '.object-picker__query'
    );
    if (search) {
        search.focus();
        if (search.value) search.value = '';
    }
} catch (e) {}

const ajaxUrl = selectEl ? (
    selectEl.getAttribute('data-ca-data-url') ||
    selectEl.getAttribute('data-ca-result-url') ||
    selectEl.getAttribute('data-ca-load-url') || ''
) : '';

return {
    opened: opened,
    hasSelect: !!selectEl,
    selectName: selectEl ? (selectEl.getAttribute('name') || '') : '',
    selectId: selectEl ? (selectEl.id || '') : '',
    clickTarget: clickTarget,
    ajaxUrl: ajaxUrl || '',
    rootScore: pickerRoot ? (pickerRoot.__asfScore || 0) : 0
};
"""

SCRAPE_CATEGORY_DROPDOWN_SCRIPT = r"""
const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$) || null;
const selectEl = window.__ASF_CATEGORY_SELECT || null;
const items = [];
const seen = new Set();

function isAdminNoise(label) {
    const low = String(label || '').toLowerCase();
    return /(alexbranding|cart-power|cs-cart|add-on|addon market|storefront|my add-ons|all add-ons|გადახდის მეთოდ|ტრანსპორტირ)/i.test(low);
}

function push(value, label, selected, field_name) {
    value = String(value == null ? '' : value).trim();
    // Prefer first line as product category name (second line = parent path)
    label = String(label || '').replace(/×/g, '').trim();
    const lines = label.split(/[\n\r]+/).map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
    if (lines.length) {
        // Use primary name, keep path in path field later
        label = lines[0];
    } else {
        label = label.replace(/\s+/g, ' ').trim();
    }
    if (!label || label.length < 2) return;
    if (label === 'ა' || label === 'a') return;
    if (isAdminNoise(label)) return;
    const low = label.toLowerCase();
    if (low.includes('ჩატვირთვ') || low.includes('loading') || low.includes('searching') ||
        low.includes('no result') || low.includes('create') || low.includes('ახალ') ||
        low.includes('დამატ') || low.includes('მიმდინარე')) return;
    if (label === '-' || label === '—') return;
    const key = value + '|' + label;
    if (seen.has(key)) return;
    seen.add(key);
    items.push({
        id: value || label,
        value: value || label,
        label: label,
        field_name: field_name || (selectEl ? (selectEl.getAttribute('name') || '') : ''),
        selected: !!selected,
        path: lines.length > 1 ? lines.join(' / ') : label
    });
}

// ONLY results from the currently OPEN product category picker — not other selects on the page
const resultRoots = [];
document.querySelectorAll(
    '.select2-container--open .select2-results, ' +
    '.select2-dropdown .select2-results, ' +
    '.select2-container--open .select2-results__options, ' +
    '.object-picker__results--open, .object-picker__results'
).forEach(r => resultRoots.push(r));

// If object-picker places results outside, still limit to options belonging to our select2
if (!resultRoots.length && selectEl && $ && $(selectEl).data('select2')) {
    try {
        const s2 = $(selectEl).data('select2');
        if (s2.$results && s2.$results[0]) resultRoots.push(s2.$results[0]);
        if (s2.$dropdown && s2.$dropdown[0]) {
            const rr = s2.$dropdown[0].querySelector('.select2-results, .select2-results__options');
            if (rr) resultRoots.push(rr);
        }
    } catch (e) {}
}

const optionNodes = [];
resultRoots.forEach(root => {
    root.querySelectorAll(
        '.select2-results__option, .select2-result-label, li.select2-result, ' +
        '.object-picker__result, li, [role="option"]'
    ).forEach(n => optionNodes.push(n));
});

optionNodes.forEach(node => {
    if (node.classList.contains('loading-results') ||
        node.classList.contains('select2-results__option--load-more') ||
        node.classList.contains('select2-results__message') ||
        node.getAttribute('data-select2-tag') === 'true') return;
    // Skip nested non-options
    if (node.closest('.select2-results__option') && !node.classList.contains('select2-results__option') &&
        node.tagName !== 'LI') {
        // keep if it's the main selectable li
    }

    let label = '';
    let value = node.getAttribute('data-ca-id') || node.getAttribute('data-id') ||
        node.getAttribute('data-value') || '';

    try {
        if ($ && $(node).data('data')) {
            const d = $(node).data('data');
            if (d) {
                if (d.newTag || d.loading) return;
                if (d.id != null) value = String(d.id);
                label = String(d.text || d.name || d.label || (d.data && (d.data.name || d.data.text)) || '');
                // Keep multi-line name + parent path in label for push()
                if (d.path) label = (label ? label + '\n' : '') + String(d.path);
                else if (d.data && d.data.path) label = (label ? label + '\n' : '') + String(d.data.path);
            }
        }
    } catch (e) {}

    if (!label) {
        const nameNode = node.querySelector(
            '.object-picker__name, .object-picker__selection-text, .select2-result-label, ' +
            '.select2-selection__choice__display, strong, b, .title, .name'
        );
        if (nameNode) label = nameNode.innerText || nameNode.textContent || '';
    }
    if (!label) label = node.getAttribute('title') || node.innerText || node.textContent || '';

    // Prefer structured: primary name line + secondary path line
    try {
        const primary = node.querySelector(
            '.object-picker__selection-text, .object-picker__name, .select2-result-label .object-picker__name'
        );
        const secondary = node.querySelector(
            '.object-picker__path, .object-picker__selection-secondary, ' +
            '.object-picker__category-path, .select2-result-label small, small'
        );
        if (primary) {
            const p = (primary.innerText || primary.textContent || '').trim();
            const s = secondary ? (secondary.innerText || secondary.textContent || '').trim() : '';
            if (p) label = s ? (p + '\n' + s) : p;
        }
    } catch (e) {}

    const idAttr = node.getAttribute('id') || '';
    const m = idAttr.match(/result-[^-]+-(\d+)/) || idAttr.match(/-(\d+)$/);
    if (m && !value) value = m[1];

    // For multi-line (name + parent path), keep full text for push() to split
    const selected = node.getAttribute('aria-selected') === 'true' ||
        node.classList.contains('select2-results__option--selected');
    if (label) push(value, label, selected, selectEl ? selectEl.getAttribute('name') : '');
});

// Options already on THIS product category select only (selected + injected)
if (selectEl) {
    Array.from(selectEl.options || []).forEach(o => {
        const lab = (o.getAttribute('data-asf-label') || o.textContent || o.label || '').trim();
        if (!lab && !o.value) return;
        if (isAdminNoise(lab)) return;
        push(o.value, lab || o.value, !!o.selected, selectEl.getAttribute('name') || '');
    });
}

// Inject scraped labels into the product category select only
if (selectEl) {
    items.forEach(it => {
        if (!it.value || !/^\d+$/.test(String(it.value))) return;
        let opt = Array.from(selectEl.options).find(o => String(o.value) === String(it.value));
        if (!opt) {
            opt = document.createElement('option');
            opt.value = it.value;
            selectEl.appendChild(opt);
        }
        if (it.label && !/^\d+$/.test(it.label)) {
            opt.textContent = it.label;
            try { opt.setAttribute('data-asf-label', it.label); } catch (e) {}
        }
        if (it.selected) opt.selected = true;
    });
}

// Close only when requested
const shouldClose = !!arguments[0];
if (shouldClose) {
    try {
        if ($ && selectEl && $(selectEl).data('select2')) $(selectEl).select2('close');
    } catch (e) {}
    try {
        if ($ && $.fn && $.fn.select2) {
            $('select').each(function () {
                try { if ($(this).data('select2')) $(this).select2('close'); } catch (e) {}
            });
        }
    } catch (e) {}
    try { document.body.click(); } catch (e) {}
}

return {
    count: items.length,
    items: items,
    sample: items.slice(0, 10),
    selectName: selectEl ? (selectEl.getAttribute('name') || '') : '',
    openResults: resultRoots.length
};
"""

# AJAX: harvest + one-page load for product category picker (paged from Python — avoids freeze)
CATEGORY_DISCOVER_SEEDS_SCRIPT = r"""
const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$) || null;
const selectEl = window.__ASF_CATEGORY_SELECT || null;
const seeds = [];
function addSeed(u) {
    if (!u) return;
    const s = String(u).trim();
    if (!s || seeds.indexOf(s) >= 0) return;
    seeds.push(s);
}
function isSafeAdminAjax(url) {
    const s = String(url || '');
    if (!s) return false;
    if (/\/api(\/|$|\?)/i.test(s)) return false;
    if (/dispatch=auth/i.test(s) || /dispatch=login/i.test(s)) return false;
    if (/dispatch=categories\.manage/i.test(s)) return false;
    try {
        const u = new URL(s, location.href);
        if (u.origin !== location.origin) return false;
    } catch (e) {}
    return true;
}
if (selectEl) {
    [
        'data-ca-data-url', 'data-ca-result-url', 'data-ca-load-url', 'data-ca-ajax-url',
        'data-url', 'data-ca-picker-url', 'data-ca-object-picker-ajax-url'
    ].forEach(a => addSeed(selectEl.getAttribute(a)));
    try {
        const root = selectEl.closest(
            '[data-ca-data-url], [data-ca-object-picker], .object-picker, .cm-object-picker, .control-group'
        );
        if (root) {
            [
                'data-ca-data-url', 'data-ca-result-url', 'data-ca-load-url',
                'data-ca-picker-url', 'data-url'
            ].forEach(a => addSeed(root.getAttribute(a)));
        }
    } catch (e) {}
    try {
        if ($ && $(selectEl).data()) {
            const d = $(selectEl).data();
            ['caDataUrl', 'caResultUrl', 'caLoadUrl', 'ajaxUrl', 'url', 'caPickerUrl'].forEach(k => addSeed(d[k]));
        }
    } catch (e) {}
    try {
        if ($ && $(selectEl).data('select2')) {
            const s2 = $(selectEl).data('select2');
            const ajax = s2.options && s2.options.get ? s2.options.get('ajax') : (s2.options && s2.options.ajax);
            if (ajax && ajax.url) {
                try {
                    const u = typeof ajax.url === 'function' ? ajax.url({ term: '', page: 1 }) : ajax.url;
                    addSeed(u);
                } catch (e) {}
            }
        }
    } catch (e) {}
}
// Live XHR from opening the picker (most reliable CS-Cart source)
(window.__ASF_NET || []).slice().reverse().forEach(entry => {
    if (!entry || !entry.url) return;
    const u = String(entry.url);
    if (/categor|picker|object|select2|tools\.list|items_list/i.test(u)) addSeed(u.split('#')[0]);
});
const baseScript = location.href.split('?')[0];
// Fallbacks known for many CS-Cart admins
[
    baseScript + '?dispatch=categories.picker',
    baseScript + '?dispatch=categories.picker&picker_for=products',
    baseScript + '?dispatch=categories.picker&object_type=categories',
    baseScript + '?dispatch=categories.get_categories_list',
    baseScript + '?dispatch=categories.get_categories_list&show_all=Y',
    baseScript + '?dispatch=categories.get_categories_list&plain=Y&show_all=Y',
    baseScript + '?dispatch=tools.list&object=categories',
    baseScript + '?dispatch=categories.picker&predefined_variants=Y'
].forEach(addSeed);

const safe = seeds.filter(isSafeAdminAjax);
// Prefer real picker URLs over guessed list endpoints
safe.sort((a, b) => {
    const score = (u) => {
        let s = 0;
        const x = String(u).toLowerCase();
        if (x.includes('data-ca') || x.includes('picker')) s += 5;
        if (x.includes('categor')) s += 3;
        if (x.includes('get_categories_list')) s += 2;
        if (x.includes('page=')) s += 1;
        return -s;
    };
    return score(a) - score(b);
});
return {
    seeds: safe.slice(0, 10),
    hasSelect: !!selectEl,
    selectName: selectEl ? (selectEl.getAttribute('name') || '') : '',
    ajaxUrlAttr: selectEl ? (selectEl.getAttribute('data-ca-data-url') || '') : '',
    optionCount: selectEl ? Array.from(selectEl.options || []).filter(o => o.value).length : 0,
    selectedCount: selectEl ? Array.from(selectEl.options || []).filter(o => o.value && o.selected).length : 0
};
"""

CATEGORY_AJAX_ONE_PAGE_SCRIPT = r"""
const seedIn = arguments[0] || '';
const page = Number(arguments[1] || 1) || 1;
const pageSize = Number(arguments[2] || 100) || 100;
const term = arguments[3] == null ? '' : String(arguments[3]);
const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$) || null;
const selectEl = window.__ASF_CATEGORY_SELECT || null;
if (!selectEl) return { ok: false, reason: 'no_select', added: 0, items: [] };
if (!seedIn) return { ok: false, reason: 'no_seed', added: 0, items: [] };

function isAdminNoise(label) {
    return /(alexbranding|cart-power|cs-cart georgia|add-on market|addon market|storefronts?|my add-ons|all add-ons|გადახდის მეთოდ|ტრანსპორტირ)/i.test(String(label || ''));
}
function cleanLab(lab) {
    lab = String(lab || '').replace(/×/g, '').trim();
    const lines = lab.split(/[\n\r]+/).map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
    return lines[0] || lab.replace(/\s+/g, ' ').trim();
}
function fullPathFromLab(lab) {
    lab = String(lab || '').replace(/×/g, '').trim();
    const lines = lab.split(/[\n\r]+/).map(s => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
    if (lines.length > 1) return lines.join(' / ');
    return lines[0] || lab.replace(/\s+/g, ' ').trim();
}
function putOption(val, lab, selected, meta) {
    val = String(val == null ? '' : val).trim();
    meta = meta || {};
    const rawLab = String(lab || '');
    lab = cleanLab(lab);
    if (!val || !lab || isAdminNoise(lab) || lab.length < 2) return false;
    if (/^\d+$/.test(lab) && lab === val) return false;
    let opt = Array.from(selectEl.options || []).find(o => String(o.value) === String(val));
    const existed = !!opt;
    if (!opt) {
        opt = document.createElement('option');
        opt.value = val;
        selectEl.appendChild(opt);
    }
    opt.textContent = lab;
    try { opt.setAttribute('data-asf-label', lab); } catch (e) {}
    const path = String(meta.path || fullPathFromLab(rawLab) || lab);
    const parentId = String(meta.parent_id != null ? meta.parent_id : (meta.parentId || ''));
    const level = meta.level != null ? String(meta.level) : '';
    try {
        if (path) opt.setAttribute('data-asf-path', path);
        if (parentId) opt.setAttribute('data-asf-parent', parentId);
        if (level !== '') opt.setAttribute('data-asf-level', level);
    } catch (e) {}
    if (selected) opt.selected = true;
    return !existed;
}
function parseBody(body) {
    const out = { count: 0, total: 0, more: false, added: 0 };
    if (!body) return out;
    const t = String(body).trim();
    let data = null;
    try {
        if (t[0] === '{' || t[0] === '[') data = JSON.parse(t);
        else {
            const i = Math.min(
                t.indexOf('{') >= 0 ? t.indexOf('{') : 1e9,
                t.indexOf('[') >= 0 ? t.indexOf('[') : 1e9
            );
            if (i < 1e9) data = JSON.parse(t.slice(i));
        }
    } catch (e) { data = null; }

    const rows = [];
    if (Array.isArray(data)) {
        rows.push(...data);
    } else if (data && typeof data === 'object') {
        out.total = Number(
            data.total_objects || data.total || data.recordsTotal ||
            (data.params && data.params.total_items) || 0
        ) || 0;
        if (data.pagination && data.pagination.more) out.more = true;
        if (data.more === true || data.has_more === true) out.more = true;
        let arr = data.objects || data.results || data.categories || data.items ||
            data.data || data.list || data.aaData || null;
        if (Array.isArray(arr)) rows.push(...arr);
        if (!rows.length && data.categories && typeof data.categories === 'object' && !Array.isArray(data.categories)) {
            Object.keys(data.categories).forEach(k => {
                rows.push(Object.assign({ id: k }, data.categories[k] || {}));
            });
        }
        if (!rows.length && data.content && typeof data.content === 'object') {
            const c = data.content;
            const arr2 = c.objects || c.categories || c.items || c.results;
            if (Array.isArray(arr2)) rows.push(...arr2);
        }
    }

    function walk(row, inheritedParent, inheritedPath) {
        if (!row) return;
        if (Array.isArray(row)) { row.forEach(r => walk(r, inheritedParent, inheritedPath)); return; }
        if (typeof row !== 'object') return;
        let val = row.id != null ? row.id : (row.category_id != null ? row.category_id :
            (row.value != null ? row.value : (row.object_id != null ? row.object_id :
            (row.cid != null ? row.cid : (row[0] != null ? row[0] : '')))));
        let lab = row.text || row.name || row.category || row.label || row.title ||
            row.category_name || row[1] || '';
        if (row.data) {
            if (!lab) lab = row.data.name || row.data.text || row.data.category || row.data.category_name || '';
            if ((val === '' || val == null) && row.data.id != null) val = row.data.id;
            if ((val === '' || val == null) && row.data.category_id != null) val = row.data.category_id;
        }
        val = String(val == null ? '' : val);
        const rawLab = String(lab || '');
        lab = cleanLab(lab);
        let parentId = row.parent_id != null ? row.parent_id :
            (row.parentId != null ? row.parentId :
            (row.parent != null && (typeof row.parent === 'string' || typeof row.parent === 'number') ? row.parent :
            (row.data && row.data.parent_id != null ? row.data.parent_id : inheritedParent)));
        parentId = parentId != null && parentId !== '' ? String(parentId) : (inheritedParent || '');
        let level = row.level != null ? row.level : (row.data && row.data.level != null ? row.data.level : null);
        // Breadcrumb / id_path style paths
        let pathHint = row.path || row.category_path || row.full_name || row.id_path_names ||
            (row.data && (row.data.path || row.data.category_path || row.data.full_name)) || '';
        if (Array.isArray(pathHint)) pathHint = pathHint.filter(Boolean).join(' / ');
        pathHint = String(pathHint || '').trim();
        let pathParts = [];
        if (pathHint && /[/>›»|]/.test(pathHint) && !/^\d+(\/\d+)*$/.test(pathHint)) {
            pathParts = pathHint.split(/\s*[/>›»|]+\s*/).map(s => s.trim()).filter(Boolean);
        } else if (inheritedPath && inheritedPath.length) {
            pathParts = inheritedPath.concat(lab ? [lab] : []);
        } else if (fullPathFromLab(rawLab).includes(' / ')) {
            pathParts = fullPathFromLab(rawLab).split(' / ').map(s => s.trim()).filter(Boolean);
        } else if (lab) {
            pathParts = [lab];
        }
        // Root-first path when path ends with this label
        let pathStr = pathParts.join(' / ') || lab;
        if (val && lab && putOption(val, lab, !!row.selected, {
            path: pathStr,
            parent_id: parentId,
            level: level
        })) out.added += 1;
        if (val && lab) out.count += 1;
        const nextPath = pathParts.length ? pathParts : (lab ? [lab] : []);
        const kids = row.children || row.subcategories || row.sub_categories ||
            row.items || row.subitems || row.list || row.nodes || null;
        if (Array.isArray(kids) && kids.length) kids.forEach(k => walk(k, val || parentId, nextPath));
        if (row.child && typeof row.child === 'object') {
            if (Array.isArray(row.child)) row.child.forEach(k => walk(k, val || parentId, nextPath));
            else Object.keys(row.child).forEach(k => walk(Object.assign({ id: k }, row.child[k]), val || parentId, nextPath));
        }
    }
    rows.forEach(r => walk(r, '', []));

    if (out.count === 0 && t.includes('<')) {
        try {
            const doc = new DOMParser().parseFromString(t, 'text/html');
            doc.querySelectorAll(
                'option, a[href*="category_id"], [data-ca-id], li[data-ca-id], ' +
                '.object-picker__result, .cm-category, [data-ca-category-id], ' +
                '.select2-results__option'
            ).forEach(node => {
                let val = node.getAttribute('value') || node.getAttribute('data-ca-id') ||
                    node.getAttribute('data-ca-category-id') || node.getAttribute('data-id') || '';
                let labRaw = node.getAttribute('title') || node.innerText || node.textContent || '';
                let lab = cleanLab(labRaw);
                let parentId = node.getAttribute('data-ca-parent-id') ||
                    node.getAttribute('data-parent-id') || '';
                let level = node.getAttribute('data-ca-level') || node.getAttribute('data-level') || '';
                // Indentation depth from padding/class as level hint
                if (level === '') {
                    const cls = node.className || '';
                    const mLevel = cls.match(/level[_-]?(\d+)/i) || cls.match(/depth[_-]?(\d+)/i);
                    if (mLevel) level = mLevel[1];
                }
                if (!val) {
                    const m = (node.getAttribute('href') || '').match(/category_id=(\d+)/);
                    if (m) val = m[1];
                }
                if (putOption(val, lab, false, {
                    path: fullPathFromLab(labRaw) || lab,
                    parent_id: parentId,
                    level: level
                })) { out.added += 1; out.count += 1; }
                else if (val && lab) out.count += 1;
            });
        } catch (e) {}
    }
    return out;
}

function looksLikeLoginHtml(body) {
    const t = String(body || '').toLowerCase();
    if (!t) return false;
    return (
        t.includes('dispatch=auth.login') ||
        t.includes('name="user_login"') ||
        (t.includes('name="password"') && t.includes('login') && t.includes('<form'))
    );
}

function withPage(url, page, pageSize, term) {
    try {
        const u = new URL(url, location.href);
        u.searchParams.set('page', String(page));
        u.searchParams.set('page_size', String(pageSize));
        u.searchParams.set('items_per_page', String(pageSize));
        u.searchParams.set('q', term);
        u.searchParams.set('search_query', term);
        u.searchParams.set('term', term);
        u.searchParams.set('search', term);
        u.searchParams.set('start', String((page - 1) * pageSize));
        u.searchParams.set('length', String(pageSize));
        u.searchParams.set('is_ajax', '1');
        if (!u.searchParams.has('show_all')) u.searchParams.set('show_all', 'Y');
        // Keep absolute for same origin
        return u.toString();
    } catch (e) {
        const joiner = String(url).includes('?') ? '&' : '?';
        return url + joiner + 'page=' + page + '&page_size=' + pageSize +
            '&items_per_page=' + pageSize +
            '&q=' + encodeURIComponent(term) +
            '&term=' + encodeURIComponent(term) +
            '&show_all=Y&is_ajax=1';
    }
}

function fetchGet(u) {
    try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', u, false);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');
        xhr.send(null);
        if (xhr.status >= 200 && xhr.status < 400) return xhr.responseText || '';
    } catch (e) {}
    try {
        if ($) {
            let body = '';
            $.ajax({
                url: u, async: false, dataType: 'text', cache: false, method: 'GET',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                success: function (b) { body = b; }
            });
            return body || '';
        }
    } catch (e) {}
    return '';
}

function fetchPost(u) {
    try {
        if ($) {
            let body = '';
            const data = {
                page: page, page_size: pageSize, items_per_page: pageSize,
                q: term, term: term, search: term, search_query: term,
                start: (page - 1) * pageSize, length: pageSize,
                is_ajax: 1, show_all: 'Y'
            };
            $.ajax({
                url: u, async: false, dataType: 'text', cache: false, method: 'POST',
                data: data,
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                success: function (b) { body = b; }
            });
            return body || '';
        }
    } catch (e) {}
    try {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', u, false);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded; charset=UTF-8');
        xhr.setRequestHeader('Accept', 'application/json, text/javascript, */*; q=0.01');
        const bodyStr = [
            'page=' + encodeURIComponent(page),
            'page_size=' + encodeURIComponent(pageSize),
            'items_per_page=' + encodeURIComponent(pageSize),
            'q=' + encodeURIComponent(term),
            'term=' + encodeURIComponent(term),
            'is_ajax=1',
            'show_all=Y'
        ].join('&');
        xhr.send(bodyStr);
        if (xhr.status >= 200 && xhr.status < 400) return xhr.responseText || '';
    } catch (e) {}
    return '';
}

const before = Array.from(selectEl.options || []).filter(o => o.value).length;
const url = withPage(seedIn, page, pageSize, term);
let body = fetchGet(url);
let method = 'GET';
let parsed = parseBody(body);
if (looksLikeLoginHtml(body)) {
    return { ok: false, reason: 'login', added: 0, items: [], page: page };
}
// If GET empty, try POST on base seed (some object-pickers POST)
if (parsed.count === 0) {
    const postBody = fetchPost(seedIn);
    if (postBody && !looksLikeLoginHtml(postBody)) {
        const p2 = parseBody(postBody);
        if (p2.count > 0) {
            parsed = p2;
            body = postBody;
            method = 'POST';
        }
    }
}

// Also absorb any net capture bodies from this round
(window.__ASF_NET || []).slice(-15).forEach(entry => {
    if (!entry || !entry.body) return;
    const u = String(entry.url || '');
    if (u && /\/api(\/|$|\?)/i.test(u)) return;
    if (u && !/categor|picker|object|select2|tools\.list|items_list/i.test(u)) return;
    if (looksLikeLoginHtml(entry.body)) return;
    const p = parseBody(entry.body);
    parsed.count += p.count;
    parsed.added += p.added;
    if (p.more) parsed.more = true;
    if (p.total > parsed.total) parsed.total = p.total;
});

const fieldName = selectEl.getAttribute('name') || '';
const list = Array.from(selectEl.options || []).map(o => ({
    value: String(o.value || ''),
    label: String((o.getAttribute('data-asf-label') || o.textContent || '').trim() || o.value || ''),
    selected: !!o.selected,
    field_name: fieldName,
    id: String(o.value || ''),
    path: String(o.getAttribute('data-asf-path') || o.getAttribute('data-asf-label') || o.textContent || '').trim(),
    parent_id: String(o.getAttribute('data-asf-parent') || ''),
    level: o.getAttribute('data-asf-level') != null && o.getAttribute('data-asf-level') !== ''
        ? Number(o.getAttribute('data-asf-level')) : null
})).filter(x => x.label && x.value && !isAdminNoise(x.label) && !/^\d+$/.test(x.label));

const after = list.length;
const unselected = list.filter(x => !x.selected).length;

return {
    ok: true,
    page: page,
    method: method,
    parsedCount: parsed.count,
    added: Math.max(0, after - before),
    more: !!parsed.more || (parsed.count >= pageSize),
    total: parsed.total || 0,
    options: after,
    unselected: unselected,
    selected: list.filter(x => x.selected).length,
    items: list,
    bodyLen: (body || '').length
};
"""

# Keep alias so any leftover references still work (runs multi-page internally, still capped)
CATEGORY_AJAX_LOAD_SCRIPT = r"""
// Compatibility stub: load first pages of discovered seeds only (short). Prefer Python paging.
return (function () {
  // Will be driven page-by-page from Python; this stub only returns current options.
  const selectEl = window.__ASF_CATEGORY_SELECT || null;
  if (!selectEl) return { ok: false, items: [], options: 0 };
  function isAdminNoise(label) {
    return /(alexbranding|cart-power|cs-cart georgia|add-on market|addon market|storefronts?)/i.test(String(label || ''));
  }
  const fieldName = selectEl.getAttribute('name') || '';
  const list = Array.from(selectEl.options || []).map(o => ({
    value: String(o.value || ''),
    label: String((o.getAttribute('data-asf-label') || o.textContent || '').trim() || o.value || ''),
    selected: !!o.selected,
    field_name: fieldName,
    id: String(o.value || '')
  })).filter(x => x.label && x.value && !isAdminNoise(x.label) && !/^\d+$/.test(x.label));
  return { ok: true, items: list, options: list.length, loaded: 0, pagesTried: 0 };
})();
"""


# ---------------------------------------------------------------------------
# Full store category tree from admin Categories → Manage
# https://acoustic.ge/aco_st_admin.php?dispatch=categories.manage
#
# Strategy (most reliable first):
#   1) Sync AJAX: categories.get_categories_list / picker / tools.list
#      → rows with parent_id / id_path (no UI expand needed)
#   2) Expand each on_cat_* / caret and re-scrape from DOM
#   3) DOM parent via enclosing #cat_{parentId} (CS-Cart tree containers)
# ---------------------------------------------------------------------------

FETCH_FULL_CATEGORY_TREE_AJAX_SCRIPT = r"""
// Pull the whole category catalog with parent links via admin AJAX (no caret clicks).
const baseScript = (location.href || '').split('?')[0] || location.pathname;
const byId = {};
const stats = { urls: 0, parsed: 0, sources: [] };

function cleanLab(s) {
    return String(s || '').replace(/×/g, '').replace(/\s+/g, ' ').trim();
}
function isNoise(lab) {
    return !lab || lab.length < 2 ||
        /(alexbranding|cart-power|cs-cart|add-on market|addon market|storefront|my add-ons|all add-ons|გადახდის|ტრანსპორტირ|^მაღაზია\s*:)/i.test(lab) ||
        /^(on|off|active|disabled|ჩართული|გამორთული|enabled|hidden)$/i.test(lab) ||
        /^\d+$/.test(lab);
}
function put(id, lab, parentId, level, path) {
    id = String(id == null ? '' : id).trim();
    lab = cleanLab(lab);
    parentId = parentId != null && parentId !== '' && parentId !== '0' ? String(parentId) : '';
    if (!id || !/^\d+$/.test(id) || isNoise(lab)) return false;
    if (id === parentId) parentId = '';
    const prev = byId[id];
    if (!prev) {
        byId[id] = {
            id: id, value: id, label: lab, parent_id: parentId,
            level: level == null ? null : Number(level),
            path: path || lab, selected: false, field_name: '',
            source: 'categories.ajax'
        };
        return true;
    }
    if (parentId && !prev.parent_id) prev.parent_id = parentId;
    if (lab && (prev.label === prev.id || /^\d+$/.test(prev.label))) prev.label = lab;
    if (path && (!prev.path || prev.path === prev.label)) prev.path = path;
    if (level != null && prev.level == null) prev.level = Number(level);
    return false;
}
function getSync(url) {
    try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', url, false);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.setRequestHeader('Accept', 'application/json, text/javascript, text/html, */*; q=0.01');
        xhr.send(null);
        if (xhr.status >= 200 && xhr.status < 400) return String(xhr.responseText || '');
    } catch (e) {}
    try {
        const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$);
        if ($) {
            let body = '';
            $.ajax({
                url: url, async: false, dataType: 'text', cache: false, method: 'GET',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                success: function (b) { body = b; }
            });
            return String(body || '');
        }
    } catch (e) {}
    return '';
}
function walkJson(row, inheritedParent, depth) {
    if (!row) return;
    if (Array.isArray(row)) { row.forEach(r => walkJson(r, inheritedParent, depth)); return; }
    if (typeof row !== 'object') return;
    let val = row.category_id != null ? row.category_id :
        (row.id != null ? row.id : (row.value != null ? row.value :
        (row.object_id != null ? row.object_id : (row.cid != null ? row.cid : ''))));
    let lab = row.category || row.category_name || row.name || row.text ||
        row.label || row.title || '';
    let parentId = row.parent_id != null ? row.parent_id :
        (row.parentId != null ? row.parentId :
        (row.parent != null && (typeof row.parent === 'string' || typeof row.parent === 'number')
            ? row.parent : inheritedParent));
    // id_path "1/5/12" → parent is previous segment
    let idPath = row.id_path || row.idPath || (row.data && row.data.id_path) || '';
    if (idPath && typeof idPath === 'string' && idPath.indexOf('/') >= 0) {
        const segs = idPath.split('/').filter(Boolean);
        if (segs.length >= 2 && !parentId) parentId = segs[segs.length - 2];
        if (segs.length) val = val || segs[segs.length - 1];
        if (parentId == null || parentId === '') parentId = segs.length >= 2 ? segs[segs.length - 2] : '';
    }
    if (row.data && typeof row.data === 'object') {
        if (!lab) lab = row.data.category || row.data.name || row.data.text || '';
        if ((val === '' || val == null) && row.data.category_id != null) val = row.data.category_id;
        if ((val === '' || val == null) && row.data.id != null) val = row.data.id;
        if ((parentId === '' || parentId == null) && row.data.parent_id != null) parentId = row.data.parent_id;
    }
    val = String(val == null ? '' : val);
    lab = cleanLab(lab);
    parentId = parentId != null && parentId !== '' ? String(parentId) : '';
    let level = row.level != null ? row.level : (row.data && row.data.level != null ? row.data.level : depth);
    if (val && lab) put(val, lab, parentId, level, lab);
    const kids = row.children || row.subcategories || row.sub_categories ||
        row.items || row.subitems || row.nodes || null;
    if (Array.isArray(kids)) kids.forEach(k => walkJson(k, val || parentId, (depth || 0) + 1));
    if (row.child && typeof row.child === 'object') {
        if (Array.isArray(row.child)) row.child.forEach(k => walkJson(k, val || parentId, (depth || 0) + 1));
        else Object.keys(row.child).forEach(k =>
            walkJson(Object.assign({ id: k }, row.child[k]), val || parentId, (depth || 0) + 1));
    }
}
function parseBody(body, sourceTag) {
    if (!body || body.length < 4) return 0;
    let added = 0;
    const before = Object.keys(byId).length;
    // JSON
    try {
        let data = null;
        const t = String(body).trim();
        if (t[0] === '{' || t[0] === '[') data = JSON.parse(t);
        else {
            const i = Math.min(
                t.indexOf('{') >= 0 ? t.indexOf('{') : 1e9,
                t.indexOf('[') >= 0 ? t.indexOf('[') : 1e9
            );
            if (i < 1e9) data = JSON.parse(t.slice(i));
        }
        if (data != null) {
            if (Array.isArray(data)) walkJson(data, '', 0);
            else if (typeof data === 'object') {
                const arr = data.objects || data.results || data.categories || data.items ||
                    data.data || data.list || data.aaData || data.rows || null;
                if (Array.isArray(arr)) walkJson(arr, '', 0);
                else if (data.categories && typeof data.categories === 'object' && !Array.isArray(data.categories)) {
                    Object.keys(data.categories).forEach(k =>
                        walkJson(Object.assign({ category_id: k }, data.categories[k]), '', 0));
                }
                // Map form category_id => name
                if (data.category_ids && typeof data.category_ids === 'object') {
                    Object.keys(data.category_ids).forEach(k => {
                        const v = data.category_ids[k];
                        if (typeof v === 'string') put(k, v, '', null, v);
                        else if (v && typeof v === 'object') walkJson(Object.assign({ category_id: k }, v), '', 0);
                    });
                }
                // CS-Cart often returns html as { "cat_12": "<tr>…", "content_…": "…" }
                if (data.html != null) absorbHtmlField(data.html, null);
                if (data.content && typeof data.content === 'string') parseHtml(data.content, null);
                if (data.text && typeof data.text === 'string') parseHtml(data.text, null);
            }
        }
    } catch (e) {}
    // HTML fragments (expand AJAX)
    if (String(body).indexOf('<') >= 0) parseHtml(body, null);
    added = Object.keys(byId).length - before;
    if (added > 0) {
        stats.parsed += added;
        stats.sources.push(sourceTag + ':' + added);
    }
    return added;
}
function absorbHtmlField(htmlField, forceParent) {
    if (htmlField == null) return;
    if (typeof htmlField === 'string') {
        parseHtml(htmlField, forceParent);
        return;
    }
    if (typeof htmlField === 'object') {
        Object.keys(htmlField).forEach(k => {
            let fp = forceParent;
            const mm = String(k).match(/^cat_(\d+)$/i);
            if (mm) fp = mm[1];
            const v = htmlField[k];
            if (typeof v === 'string') parseHtml(v, fp);
            else if (v && typeof v === 'object' && typeof v.html === 'string') parseHtml(v.html, fp);
        });
    }
}
function parseHtml(html, forceParent) {
    if (!html || String(html).indexOf('<') < 0) return;
    try {
        const doc = new DOMParser().parseFromString(String(html), 'text/html');
        // Prefer children container #cat_{parent} when forcing a parent (avoid
        // stamping whole manage page under one category).
        let roots = [];
        if (forceParent) {
            const cont = doc.getElementById('cat_' + forceParent);
            if (cont) roots = [cont];
            else {
                // Small AJAX fragment: use whole doc. Huge page: only #cat_* nodes.
                const cats = doc.querySelectorAll('[id^="cat_"]');
                if (cats.length) {
                    cats.forEach(c => {
                        const mm = (c.id || '').match(/^cat_(\d+)$/i);
                        // prefer exact parent container if present under nested parse
                        if (mm && mm[1] === String(forceParent)) roots.push(c);
                    });
                    if (!roots.length) {
                        // Fragment of rows only — use document; later skip known non-kids
                        roots = [doc.body || doc];
                    }
                } else {
                    roots = [doc.body || doc];
                }
            }
        } else {
            roots = [doc];
        }
        roots.forEach(scope => {
            if (!scope || !scope.querySelectorAll) return;
            scope.querySelectorAll(
                'a[href*="categories.update"][href*="category_id="], a[href*="category_id="]'
            ).forEach(a => {
            const href = a.getAttribute('href') || '';
            if (!/categories\.update|dispatch=categories\.update/i.test(href) &&
                !/category_id=\d+/i.test(href)) return;
            if (/categories\.(delete|m_delete|clone|add)/i.test(href)) return;
            const m = href.match(/category_id=(\d+)/i);
            if (!m) return;
            let lab = cleanLab(a.getAttribute('title') || a.innerText || a.textContent || '');
            lab = lab.replace(/\s*\d+\s*$/, '').trim();
            if (isNoise(lab)) return;
            let parentId = forceParent ? String(forceParent) : '';
            if (!forceParent) {
                let el = a.parentElement;
                while (el) {
                    const id = el.id || '';
                    const mm = id.match(/^cat_(\d+)$/i) || id.match(/^category[_-]?(\d+)$/i);
                    if (mm) { parentId = mm[1]; break; }
                    if (el.getAttribute) {
                        const p = el.getAttribute('data-ca-parent-id') || el.getAttribute('data-parent-id');
                        if (p) { parentId = String(p); break; }
                    }
                    el = el.parentElement;
                }
            } else {
                // still prefer deeper enclosing cat_ if present under scope
                let el = a.parentElement;
                while (el && el !== scope) {
                    const id = el.id || '';
                    const mm = id.match(/^cat_(\d+)$/i);
                    if (mm && mm[1] !== String(forceParent)) {
                        parentId = mm[1];
                        break;
                    }
                    el = el.parentElement;
                }
            }
            if (parentId === m[1]) parentId = '';
            if (forceParent && m[1] === String(forceParent)) parentId = '';
            put(m[1], lab, parentId, parentId ? 1 : 0, lab);
            });
        });
        // Nested cat_* containers
        doc.querySelectorAll('[id^="cat_"]').forEach(cont => {
            const mm = (cont.id || '').match(/^cat_(\d+)$/i);
            if (!mm) return;
            cont.querySelectorAll('a[href*="category_id="]').forEach(a => {
                const m = (a.getAttribute('href') || '').match(/category_id=(\d+)/i);
                if (!m || m[1] === mm[1]) return;
                let lab = cleanLab(a.innerText || a.textContent || '');
                if (!isNoise(lab)) put(m[1], lab, mm[1], 1, lab);
            });
        });
        // option tags
        doc.querySelectorAll('option[value]').forEach(o => {
            const id = String(o.getAttribute('value') || '').trim();
            const lab = cleanLab(o.textContent || '');
            if (id && /^\d+$/.test(id) && !isNoise(lab)) put(id, lab, '', null, lab);
        });
    } catch (e) {}
}

// Seed URLs for full catalog (few high-yield endpoints only — speed)
const seeds = [
    baseScript + '?dispatch=categories.get_categories_list&plain=Y&show_all=Y&is_ajax=1',
    baseScript + '?dispatch=categories.get_categories_list&show_all=Y&is_ajax=1',
    baseScript + '?dispatch=categories.picker&is_ajax=1&multiple=Y&show_all=Y',
    baseScript + '?dispatch=tools.list&object=categories&is_ajax=1&start=0&count=2000',
    baseScript + '?dispatch=categories.manage&is_ajax=1'
];
// Live XHR captures from opening the page
try {
    (window.__ASF_NET || []).slice().reverse().forEach(entry => {
        if (!entry || !entry.url) return;
        const u = String(entry.url);
        if (/categor|tools\.list|items_list|picker/i.test(u) && seeds.indexOf(u.split('#')[0]) < 0) {
            seeds.push(u.split('#')[0]);
        }
    });
} catch (e) {}

for (const u of seeds.slice(0, 8)) {
    stats.urls += 1;
    const body = getSync(u);
    parseBody(body, u.slice(0, 80));
}

// Root / expandable ids from current manage DOM
const rootIds = [];
document.querySelectorAll(
    'a[id^="on_cat_"], a[href*="categories.update"][href*="category_id="]'
).forEach(el => {
    let id = '';
    if (el.id && /^on_cat_/i.test(el.id)) id = el.id.replace(/^on_cat_/i, '');
    else {
        const m = (el.getAttribute('href') || '').match(/category_id=(\d+)/i);
        if (m) id = m[1];
    }
    if (id && /^\d+$/.test(id) && rootIds.indexOf(id) < 0) rootIds.push(id);
});
Object.keys(byId).forEach(id => {
    if (!byId[id].parent_id && rootIds.indexOf(id) < 0) rootIds.push(id);
});

// For each root/parent ask for its children HTML/JSON
const parentUrls = (pid) => [
    baseScript + '?dispatch=categories.manage&category_id=' + pid + '&result_ids=cat_' + pid + '&is_ajax=1',
    baseScript + '?dispatch=categories.manage&category_id=' + pid + '&is_ajax=1',
    baseScript + '?dispatch=categories.get_categories_list&parent_id=' + pid + '&is_ajax=1&show_all=Y'
];

const parentsToFetch = rootIds.slice(0, 60);
// Always also fetch parents that already have no children in byId
const needKids = parentsToFetch.filter(pid => {
    return !Object.keys(byId).some(cid => byId[cid].parent_id === pid);
});
// If enough hierarchy already, only fill a few missing parents
const withParentCount = Object.keys(byId).filter(id => byId[id].parent_id).length;
const fetchList = (withParentCount >= Math.max(8, Object.keys(byId).length * 0.15) && needKids.length)
    ? needKids.slice(0, 20)
    : (needKids.length ? needKids : parentsToFetch.slice(0, 25));

for (const pid of fetchList) {
    for (const u of parentUrls(pid)) {
        const body = getSync(u);
        parseBody(body, 'p' + pid);
        // Always stamp parent = pid for every category link in this child response
        // (flat seed lists often already know the IDs, so "added" may be 0).
        if (body) {
            try {
                const t = String(body).trim();
                if (t[0] === '{' || t[0] === '[') {
                    const data = JSON.parse(t.indexOf('{') === 0 || t.indexOf('[') === 0
                        ? t
                        : t.slice(Math.min(
                            t.indexOf('{') >= 0 ? t.indexOf('{') : 1e9,
                            t.indexOf('[') >= 0 ? t.indexOf('[') : 1e9
                        )));
                    if (data && typeof data === 'object' && !Array.isArray(data)) {
                        if (data.html != null) absorbHtmlField(data.html, pid);
                        if (typeof data.content === 'string') parseHtml(data.content, pid);
                    }
                    // JSON arrays of kids
                    walkJson(
                        Array.isArray(data) ? data
                            : (data && (data.objects || data.categories || data.items || data.results)),
                        pid, 1
                    );
                }
            } catch (e) {}
            if (String(body).indexOf('<') >= 0) {
                parseHtml(body, pid);
                try {
                    const doc = new DOMParser().parseFromString(String(body), 'text/html');
                    doc.querySelectorAll('a[href*="category_id="]').forEach(a => {
                        const m = (a.getAttribute('href') || '').match(/category_id=(\d+)/i);
                        if (!m || m[1] === pid) return;
                        let lab = cleanLab(a.innerText || a.textContent || '');
                        if (!isNoise(lab)) put(m[1], lab, pid, 1, lab);
                    });
                } catch (e) {}
            }
        }
        // stop trying other urls for this parent if we got kids
        if (Object.keys(byId).some(cid => byId[cid].parent_id === pid)) break;
    }
}

// Rebuild paths
Object.keys(byId).forEach(id => {
    const chain = [];
    let cur = byId[id];
    const guard = new Set();
    while (cur && !guard.has(cur.id)) {
        guard.add(cur.id);
        chain.unshift(cur.label);
        cur = cur.parent_id ? byId[cur.parent_id] : null;
    }
    if (chain.length) byId[id].path = chain.join(' / ');
});

const items = Object.keys(byId).map(k => byId[k]);
items.sort((a, b) => String(a.path || a.label).localeCompare(String(b.path || b.label), 'ka'));
return {
    ok: items.length > 0,
    count: items.length,
    with_parent: items.filter(i => i.parent_id).length,
    items: items,
    stats: stats,
    roots_tried: fetchList.length
};
"""

EXPAND_ALL_CARETS_SCRIPT = r"""
let clicks = 0;
const max = 80;
function tryClick(el) {
    if (!el || clicks >= max) return;
    try {
        const st = window.getComputedStyle(el);
        if (st && (st.display === 'none' || st.visibility === 'hidden')) return;
    } catch (e) {}
    try { el.scrollIntoView({ block: 'nearest' }); } catch (e) {}
    try { el.click(); clicks += 1; } catch (e) {
        try {
            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            clicks += 1;
        } catch (e2) {}
    }
}
// Only CLOSED expand controls
document.querySelectorAll('a[id^="on_cat_"]').forEach(a => {
    // if matching off_cat exists and is visible, already open
    const id = a.id.replace(/^on_cat_/i, '');
    const off = document.getElementById('off_cat_' + id);
    if (off) {
        try {
            const st = window.getComputedStyle(off);
            if (st && st.display !== 'none' && st.visibility !== 'hidden') return;
        } catch (e) {}
    }
    tryClick(a);
});
document.querySelectorAll(
    '.icon-caret-right, .cs-icon--type-caret-right, .ty-icon-right-open, ' +
    'i.icon-right-dir, .exicon-expand'
).forEach(icon => {
    const hit = icon.closest('a, button, span.cm-combination, .cm-combination') || icon;
    tryClick(hit);
});
return {
    clicks: clicks,
    carets_left: document.querySelectorAll('a[id^="on_cat_"], .icon-caret-right').length,
    rows: document.querySelectorAll('a[href*="categories.update"]').length
};
"""

EXPAND_ONE_CATEGORY_SCRIPT = r"""
const catId = String(arguments[0] || '').trim();
if (!catId) return { ok: false, reason: 'no_id' };
function clickEl(el) {
    if (!el) return false;
    try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    try { el.click(); return true; } catch (e) {
        try {
            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            return true;
        } catch (e2) { return false; }
    }
}
const on = document.getElementById('on_cat_' + catId);
if (on && clickEl(on)) return { ok: true, how: 'on_cat' };
// Also try loading child container via combination target
const byTarget = document.querySelector(
    '[data-ca-target-id="cat_' + catId + '"], a[href*="result_ids=cat_' + catId + '"]'
);
if (byTarget && clickEl(byTarget)) return { ok: true, how: 'target' };
const nameLinks = document.querySelectorAll(
    'a[href*="categories.update"][href*="category_id=' + catId + '"]'
);
for (const a of nameLinks) {
    const tr = a.closest('tr');
    if (!tr) continue;
    const exp = tr.querySelector('a[id^="on_cat_"], a.cm-combination, .icon-caret-right');
    if (exp && clickEl(exp.closest('a') || exp)) return { ok: true, how: 'row' };
}
try {
    const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$);
    if ($ && $.ceAjax) {
        const base = (location.href || '').split('?')[0];
        // trigger ajax load of children into cat_{id} (CS-Cart pattern)
        $.ceAjax('request', base + '?dispatch=categories.manage&category_id=' + catId, {
            result_ids: 'cat_' + catId,
            caching: false,
            hidden: true,
            force_exec: true
        });
        return { ok: true, how: 'ceAjax' };
    }
} catch (e) {}
return { ok: false, reason: 'not_found' };
"""

LIST_ROOT_CATEGORY_IDS_SCRIPT = r"""
const ids = [];
const seen = new Set();
document.querySelectorAll('a[id^="on_cat_"]').forEach(a => {
    const id = a.id.replace(/^on_cat_/i, '');
    if (id && /^\d+$/.test(id) && !seen.has(id)) { seen.add(id); ids.push(id); }
});
document.querySelectorAll('a[href*="categories.update"][href*="category_id="]').forEach(a => {
    if (a.closest('#header_navbar, .navbar, .sidebar, .pagination')) return;
    const m = (a.getAttribute('href') || '').match(/category_id=(\d+)/i);
    if (!m || seen.has(m[1])) return;
    // Only treat as root if appears near level-0 (no enclosing cat_*)
    let el = a.parentElement;
    let inside = false;
    while (el) {
        if (el.id && /^cat_\d+$/i.test(el.id)) { inside = true; break; }
        el = el.parentElement;
    }
    if (!inside) { seen.add(m[1]); ids.push(m[1]); }
});
return { ids: ids, count: ids.length };
"""

FETCH_CHILDREN_FOR_PARENT_SCRIPT = r"""
// Load direct child categories for one parent id (CS-Cart manage / list AJAX + #cat_*).
const pid = String(arguments[0] || '');
const base = (location.href || '').split('?')[0];
const items = [];
const seen = new Set();
function clean(s){ return String(s||'').replace(/\s+/g,' ').trim(); }
function isNoise(lab) {
  return !lab || lab.length < 2 || /^\d+$/.test(lab) ||
    /^(on|off|active|disabled|ჩართული|გამორთული|enabled|hidden)$/i.test(lab);
}
function put(id, lab){
  id = String(id == null ? '' : id);
  lab = clean(lab);
  if (!id || !/^\d+$/.test(id) || !lab || id === pid || seen.has(id) || isNoise(lab)) return;
  seen.add(id);
  items.push({
    id:id, value:id, label:lab, parent_id:pid, level:1, path:lab,
    selected:false, field_name:'', source:'categories.child_fetch'
  });
}
function get(u){
  try {
    const x = new XMLHttpRequest();
    x.open('GET', u, false);
    x.setRequestHeader('X-Requested-With','XMLHttpRequest');
    x.setRequestHeader('Accept','application/json, text/javascript, text/html, */*; q=0.01');
    x.send(null);
    return (x.status>=200 && x.status<400) ? (x.responseText||'') : '';
  } catch(e){ return ''; }
}
function parseHtml(html, forcePid) {
  if (!html || String(html).indexOf('<') < 0) return;
  try {
    const doc = new DOMParser().parseFromString(String(html), 'text/html');
    doc.querySelectorAll('a[href*="category_id="]').forEach(a => {
      const href = a.getAttribute('href') || '';
      const m = href.match(/category_id=(\d+)/i);
      if (!m || m[1] === forcePid) return;
      if (!/categories\.update|dispatch=categories\.update/i.test(href) &&
          !a.closest('tr,li,.cm-row-item,.ty-tree')) return;
      put(m[1], a.getAttribute('title') || a.innerText || a.textContent || '');
    });
  } catch(e) {}
}
function absorbBody(body) {
  if (!body) return;
  try {
    const t = String(body).trim();
    if (t[0]==='{' || t[0]==='[') {
      let data = JSON.parse(t);
      if (data && typeof data === 'object') {
        if (data.html != null) {
          if (typeof data.html === 'string') parseHtml(data.html, pid);
          else if (typeof data.html === 'object') {
            Object.keys(data.html).forEach(k => {
              const v = data.html[k];
              if (typeof v === 'string') parseHtml(v, pid);
            });
          }
        }
        if (typeof data.content === 'string') parseHtml(data.content, pid);
        const arr = Array.isArray(data) ? data :
          (data.objects || data.results || data.categories || data.items || null);
        if (Array.isArray(arr)) {
          arr.forEach(row => {
            if (!row || typeof row !== 'object') return;
            put(row.category_id || row.id, row.category || row.name || row.text || row.label);
          });
        }
      }
    }
  } catch(e) {}
  if (String(body).indexOf('<') >= 0) parseHtml(body, pid);
}
const urls = [
  base + '?dispatch=categories.manage&category_id=' + pid + '&is_ajax=1',
  base + '?dispatch=categories.manage&category_id=' + pid + '&result_ids=cat_' + pid + '&is_ajax=1',
  base + '?dispatch=categories.manage&parent_id=' + pid + '&is_ajax=1',
  base + '?dispatch=categories.get_categories_list&parent_id=' + pid + '&is_ajax=1&show_all=Y',
  base + '?dispatch=categories.get_categories_list&parent_category_id=' + pid + '&is_ajax=1',
  base + '?dispatch=categories.get_categories_list&category_id=' + pid + '&plain=Y&is_ajax=1'
];
for (const u of urls) {
  absorbBody(get(u));
  if (items.length) break;
}
// DOM under #cat_pid (after expand / ceAjax)
try {
  const cont = document.getElementById('cat_' + pid);
  if (cont) {
    cont.querySelectorAll('a[href*="category_id="]').forEach(a => {
      const m = (a.getAttribute('href')||'').match(/category_id=(\d+)/i);
      if (m) put(m[1], a.innerText || a.textContent || '');
    });
  }
  // Sibling pattern: off_cat / on_cat on same row → children inserted after that tr
  const on = document.getElementById('on_cat_' + pid) || document.getElementById('off_cat_' + pid);
  if (on) {
    let tr = on.closest('tr');
    if (tr) {
      let sib = tr.nextElementSibling;
      let hops = 0;
      while (sib && hops < 40) {
        hops += 1;
        // stop at next root-level (has own on_cat for another top parent with different pad?)
        if (sib.querySelector && sib.querySelector('#on_cat_' + pid + ', #off_cat_' + pid)) break;
        const nest = sib.id && /^cat_/.test(sib.id) ? sib : (sib.querySelector ? sib.querySelector('#cat_' + pid + ', [id^="cat_"]') : null);
        if (sib.id === 'cat_' + pid || (nest && nest.id === 'cat_' + pid)) {
          (nest || sib).querySelectorAll('a[href*="category_id="]').forEach(a => {
            const m = (a.getAttribute('href')||'').match(/category_id=(\d+)/i);
            if (m) put(m[1], a.innerText || a.textContent || '');
          });
          break;
        }
        // indent-based children rows after expand
        if (sib.querySelectorAll) {
          const links = sib.querySelectorAll('a[href*="categories.update"][href*="category_id="]');
          if (links.length && sib.querySelector('a[id^="on_cat_"], a[id^="off_cat_"]')) {
            // might be another main sibling — stop if expander is not under this parent
            const exp = sib.querySelector('a[id^="on_cat_"], a[id^="off_cat_"]');
            if (exp && exp.id && !new RegExp('_' + pid + '$').test(exp.id) && hops > 1) {
              // another category row — only take if this is inside a container under pid
              if (!sib.closest || !sib.closest('#cat_' + pid)) break;
            }
          }
          links.forEach(a => {
            if (a.closest('#cat_' + pid) || hops === 1) {
              const m = (a.getAttribute('href')||'').match(/category_id=(\d+)/i);
              if (m && m[1] !== pid) put(m[1], a.innerText || a.textContent || '');
            }
          });
        }
        sib = sib.nextElementSibling;
      }
    }
  }
} catch(e) {}
return { ok: items.length>0, count: items.length, items: items, parent_id: pid };
"""

BATCH_FETCH_CHILDREN_SCRIPT = r"""
// Fetch children for many parent ids in one round-trip (sync XHR in page).
const pids = Array.isArray(arguments[0]) ? arguments[0] : [];
const base = (location.href || '').split('?')[0];
const items = [];
const seen = new Set();
function clean(s){ return String(s||'').replace(/\s+/g,' ').trim(); }
function isNoise(lab) {
  return !lab || lab.length < 2 || /^\d+$/.test(lab) ||
    /^(on|off|active|disabled|ჩართული|გამორთული|enabled|hidden)$/i.test(lab);
}
function put(pid, id, lab){
  id = String(id == null ? '' : id);
  lab = clean(lab);
  const key = id;
  if (!id || !/^\d+$/.test(id) || !lab || id === String(pid) || seen.has(key) || isNoise(lab)) return;
  seen.add(key);
  items.push({
    id:id, value:id, label:lab, parent_id:String(pid), level:1, path:lab,
    selected:false, field_name:'', source:'categories.child_batch'
  });
}
function get(u){
  try {
    const x = new XMLHttpRequest();
    x.open('GET', u, false);
    x.setRequestHeader('X-Requested-With','XMLHttpRequest');
    x.setRequestHeader('Accept','application/json, text/javascript, text/html, */*; q=0.01');
    x.send(null);
    return (x.status>=200 && x.status<400) ? (x.responseText||'') : '';
  } catch(e){ return ''; }
}
function parseHtml(html, pid) {
  if (!html || String(html).indexOf('<') < 0) return 0;
  let n = 0;
  try {
    const doc = new DOMParser().parseFromString(String(html), 'text/html');
    const cont = doc.getElementById('cat_' + pid);
    const roots = cont ? [cont] : [doc];
    roots.forEach(scope => {
      if (!scope || !scope.querySelectorAll) return;
      scope.querySelectorAll('a[href*="category_id="]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/category_id=(\d+)/i);
        if (!m || m[1] === String(pid)) return;
        if (!/categories\.update|dispatch=categories\.update/i.test(href) &&
            !a.closest('tr,li,.cm-row-item')) return;
        put(pid, m[1], a.getAttribute('title') || a.innerText || a.textContent || '');
        n += 1;
      });
    });
  } catch(e) {}
  return n;
}
function absorbBody(body, pid) {
  if (!body) return 0;
  let before = items.length;
  try {
    const t = String(body).trim();
    if (t[0]==='{' || t[0]==='[') {
      let data = JSON.parse(t);
      if (data && typeof data === 'object') {
        if (data.html != null) {
          if (typeof data.html === 'string') parseHtml(data.html, pid);
          else if (typeof data.html === 'object') {
            Object.keys(data.html).forEach(k => {
              const v = data.html[k];
              if (typeof v === 'string') parseHtml(v, pid);
            });
          }
        }
        if (typeof data.content === 'string') parseHtml(data.content, pid);
        const arr = Array.isArray(data) ? data :
          (data.objects || data.results || data.categories || data.items || null);
        if (Array.isArray(arr)) {
          arr.forEach(row => {
            if (!row || typeof row !== 'object') return;
            put(pid, row.category_id || row.id, row.category || row.name || row.text || row.label);
          });
        }
      }
    }
  } catch(e) {}
  if (String(body).indexOf('<') >= 0) parseHtml(body, pid);
  return items.length - before;
}
let parents_ok = 0;
for (let i = 0; i < pids.length; i++) {
  const pid = String(pids[i] || '');
  if (!pid || !/^\d+$/.test(pid)) continue;
  const urls = [
    base + '?dispatch=categories.manage&category_id=' + pid + '&result_ids=cat_' + pid + '&is_ajax=1',
    base + '?dispatch=categories.manage&category_id=' + pid + '&is_ajax=1',
    base + '?dispatch=categories.get_categories_list&parent_id=' + pid + '&is_ajax=1&show_all=Y'
  ];
  let got = 0;
  for (const u of urls) {
    got = absorbBody(get(u), pid);
    if (got > 0) break;
  }
  // Live DOM if already expanded
  try {
    const cont = document.getElementById('cat_' + pid);
    if (cont) {
      cont.querySelectorAll('a[href*="category_id="]').forEach(a => {
        const m = (a.getAttribute('href')||'').match(/category_id=(\d+)/i);
        if (m) put(pid, m[1], a.innerText || a.textContent || '');
      });
    }
  } catch(e) {}
  if (items.some(it => String(it.parent_id) === pid)) parents_ok += 1;
}
return { ok: items.length > 0, count: items.length, items: items, parents_ok: parents_ok };
"""

SCRAPE_CATEGORIES_MANAGE_SCRIPT = r"""
// Parse CS-Cart categories.manage DOM — parent from #cat_{id} enclosure primarily
const items = [];
const seen = new Set();
function cleanLab(s) {
    return String(s || '').replace(/×/g, '').replace(/\s+/g, ' ').trim();
}
function isNoise(lab) {
    return !lab || lab.length < 2 || /^\d+$/.test(lab) ||
        /^(on|off|active|disabled|ჩართული|გამორთული|enabled|hidden)$/i.test(lab) ||
        /^მაღაზია\s*:/i.test(lab) ||
        /(alexbranding|cart-power|cs-cart|add-on market)/i.test(lab);
}

const candidates = [];
document.querySelectorAll('a[href*="category_id="]').forEach(a => {
    if (a.closest(
        '#header_navbar, #header_subnav, .navbar, .sidebar, .mainbox-nav, ' +
        '#actions_panel, .pagination, .nav, #menu, .top-menu'
    )) return;
    const href = a.getAttribute('href') || '';
    if (!/categories\.update/i.test(href) && !/dispatch=categories\.update/i.test(href)) return;
    if (/categories\.(delete|m_delete|clone|add|picker)/i.test(href)) return;
    const m = href.match(/category_id=(\d+)/i);
    if (!m) return;
    let lab = cleanLab(a.getAttribute('title') || a.innerText || a.textContent || '');
    lab = lab.replace(/\s*\d+\s*$/, '').trim();
    if (isNoise(lab)) return;
    const rect = a.getBoundingClientRect();
    if (!rect || rect.width < 2 || rect.height < 2) return;

    // Parent: nearest ancestor id=cat_XYZ (CS-Cart injects children inside after expand)
    let parentId = '';
    let levelHint = 0;
    let el = a.parentElement;
    while (el) {
        const id = el.id || '';
        const mm = id.match(/^cat_(\d+)$/i);
        if (mm) {
            parentId = mm[1];
            // count nesting depth of cat_ containers
            let depth = 0;
            let p = el;
            while (p) {
                if (p.id && /^cat_\d+$/i.test(p.id)) depth += 1;
                p = p.parentElement;
            }
            levelHint = depth;
            break;
        }
        if (el.getAttribute) {
            const dp = el.getAttribute('data-ca-parent-id') || el.getAttribute('data-parent-id');
            if (dp) { parentId = String(dp); break; }
        }
        el = el.parentElement;
    }
    if (parentId === m[1]) parentId = '';

    candidates.push({
        id: m[1],
        lab: lab,
        parentId: parentId,
        left: rect.left,
        top: rect.top + (window.scrollY || 0),
        levelHint: levelHint
    });
});

candidates.sort((x, y) => (x.top - y.top) || (x.left - y.left));
// unique id
const rows = [];
const idSet = new Set();
for (const c of candidates) {
    if (idSet.has(c.id)) continue;
    idSet.add(c.id);
    rows.push(c);
}

// Fallback level from indent when no #cat_ parent
const lefts = Array.from(new Set(rows.map(r => Math.round(r.left)))).sort((a, b) => a - b);
const clusters = [];
lefts.forEach(L => {
    if (!clusters.length || L - clusters[clusters.length - 1] > 14) clusters.push(L);
});
function levelFromLeft(left) {
    const L = Math.round(left);
    let best = 0, bestD = 1e9;
    clusters.forEach((c, i) => {
        const d = Math.abs(c - L);
        if (d < bestD) { bestD = d; best = i; }
    });
    return best;
}

const stack = [];
rows.forEach(r => {
    let parentId = r.parentId || '';
    let level = r.levelHint || 0;
    if (!parentId) {
        level = levelFromLeft(r.left);
        while (stack.length && stack[stack.length - 1].level >= level) stack.pop();
        parentId = stack.length ? stack[stack.length - 1].id : '';
    } else {
        // keep stack approx aligned
        level = Math.max(level, 1);
        while (stack.length && stack[stack.length - 1].id !== parentId &&
               stack[stack.length - 1].level >= level) stack.pop();
    }
    if (seen.has(r.id)) return;
    seen.add(r.id);
    const pathParts = [];
    // build path via parent walk after
    items.push({
        id: r.id,
        value: r.id,
        label: r.lab,
        parent_id: parentId,
        level: level,
        path: r.lab,
        selected: false,
        field_name: '',
        source: 'categories.manage.dom'
    });
    stack.push({ id: r.id, level: level, label: r.lab });
});

// path rebuild
const byId = {};
items.forEach(it => { byId[it.id] = it; });
items.forEach(it => {
    const chain = [];
    let cur = it;
    const guard = new Set();
    while (cur && !guard.has(cur.id)) {
        guard.add(cur.id);
        chain.unshift(cur.label);
        cur = cur.parent_id ? byId[cur.parent_id] : null;
    }
    if (chain.length) it.path = chain.join(' / ');
});

return {
    ok: items.length > 0,
    count: items.length,
    items: items,
    with_parent: items.filter(i => i.parent_id).length,
    levels: clusters.length
};
"""

READ_PRODUCT_CATEGORY_SELECTION_SCRIPT = r"""
const selected = [];
const seen = new Set();
let fieldName = '';

function isProductCategorySelect(el) {
    if (!el) return false;
    const n = (el.getAttribute('name') || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    if (n.includes('product_data') && n.includes('category')) return true;
    if (n.includes('category_ids') && !n.includes('company') && !n.includes('storefront')) return true;
    if (id.includes('product_categor')) return true;
    return false;
}

document.querySelectorAll('select').forEach(el => {
    if (!isProductCategorySelect(el)) return;
    if (!fieldName) fieldName = el.getAttribute('name') || '';
    Array.from(el.options || []).forEach(o => {
        if (!o.selected || !o.value) return;
        const key = String(o.value);
        if (seen.has(key)) return;
        seen.add(key);
        selected.push({
            id: key,
            value: key,
            label: String((o.getAttribute('data-asf-label') || o.textContent || '').trim() || key),
            selected: true
        });
    });
});

return { selected: selected, field_name: fieldName, count: selected.length };
"""

# Cache full manage tree across scrape/bulk jobs (same Chrome session)
_categories_manage_cache: list[dict[str, Any]] | None = None
_categories_manage_cache_key: str = ""


def _admin_script_url(driver) -> str:
    """Base admin entry, e.g. https://acoustic.ge/aco_st_admin.php"""
    try:
        url = str(driver.current_url or "")
    except Exception:
        url = ""
    if url and "?" in url:
        base = url.split("?")[0]
    else:
        base = url or "https://acoustic.ge/aco_st_admin.php"
    if not re.search(r"\.php$", base, re.I):
        try:
            from urllib.parse import urlparse

            p = urlparse(url or "https://acoustic.ge/aco_st_admin.php")
            base = f"{p.scheme or 'https'}://{p.netloc or 'acoustic.ge'}/aco_st_admin.php"
        except Exception:
            base = "https://acoustic.ge/aco_st_admin.php"
    return base


def _category_tree_is_flat(items: list[dict[str, Any]]) -> bool:
    """True when almost every row looks like a root (no parent_id / depth)."""
    if not items:
        return True
    with_parent = sum(1 for i in items if str(i.get("parent_id") or "").strip())
    n = len(items)
    return with_parent < max(2, int(n * 0.15))


def _merge_category_item_lists(
    base: list[dict[str, Any]], extra: list | None
) -> list[dict[str, Any]]:
    by_v: dict[str, dict] = {
        str(x.get("value") or x.get("id") or ""): dict(x) for x in base if isinstance(x, dict)
    }
    if not isinstance(extra, list):
        return list(by_v.values())
    for it in extra:
        if not isinstance(it, dict):
            continue
        lab = str(it.get("label") or "").strip()
        if not lab:
            continue
        v = str(it.get("value") or it.get("id") or lab)
        prev = by_v.get(v)
        if not prev:
            by_v[v] = {
                "id": v,
                "value": v,
                "label": lab,
                "field_name": str(it.get("field_name") or ""),
                "selected": bool(it.get("selected")),
                "path": str(it.get("path") or lab),
                "parent_id": str(it.get("parent_id") or ""),
                "level": it.get("level"),
                "source": str(it.get("source") or "categories.manage"),
            }
        else:
            if it.get("selected"):
                prev["selected"] = True
            pid = str(it.get("parent_id") or "").strip()
            if pid and not prev.get("parent_id"):
                prev["parent_id"] = pid
            new_path = str(it.get("path") or "").strip()
            if new_path and (
                not prev.get("path")
                or prev.get("path") == prev.get("label")
                or ("/" in new_path and "/" not in str(prev.get("path") or ""))
            ):
                prev["path"] = new_path
            if it.get("level") is not None and prev.get("level") is None:
                prev["level"] = it.get("level")
    return list(by_v.values())


def scrape_categories_manage_tree(
    driver,
    *,
    progress_cb=None,
    force: bool = False,
    return_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Load full category tree from categories.manage using AJAX + expand + DOM.
    Result includes parent_id so UI can open გიტარა → subcategories.
    Fast path: use session cache + AJAX-only when hierarchy is already complete.
    """
    global _categories_manage_cache, _categories_manage_cache_key

    def _prog(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    def _wp(items: list) -> int:
        return sum(1 for x in items if isinstance(x, dict) and str(x.get("parent_id") or "").strip())

    def _tree_good(items: list) -> bool:
        if not items or len(items) < 8:
            return False
        wp = _wp(items)
        # Need a real hierarchy, not only flat mains
        return wp >= max(4, int(len(items) * 0.12)) and not _category_tree_is_flat(items)

    base = _admin_script_url(driver)
    cache_key = base
    # 1) In-process memory cache
    if (
        not force
        and _categories_manage_cache
        and _categories_manage_cache_key == cache_key
        and _tree_good(_categories_manage_cache)
    ):
        n_sub = _wp(_categories_manage_cache)
        _prog(
            f"Using cached category tree "
            f"({len(_categories_manage_cache)} cats, {n_sub} subcategories)…"
        )
        return [dict(x) for x in _categories_manage_cache]

    # 2) Disk cache (survives app restarts — no navigate away from product)
    if not force:
        disk = _disk_load_categories(base)
        if disk and _tree_good(disk):
            _categories_manage_cache = [dict(x) for x in disk]
            _categories_manage_cache_key = cache_key
            n_sub = _wp(disk)
            _prog(
                f"Using saved category tree "
                f"({len(disk)} cats, {n_sub} subcategories)…"
            )
            return [dict(x) for x in disk]

    if force and _categories_manage_cache and _category_tree_is_flat(_categories_manage_cache):
        _categories_manage_cache = None

    if _on_login_page(driver):
        _prog("Login required — cannot open Categories manage.")
        return []

    manage_url = f"{base}?dispatch=categories.manage"
    back = return_url or ""
    try:
        if not back:
            back = str(driver.current_url or "")
    except Exception:
        back = ""

    try:
        driver.set_script_timeout(90)
    except Exception:
        pass

    _prog("Opening Categories manage…")
    try:
        driver.get(manage_url)
        time.sleep(0.55)
    except Exception as exc:
        _prog(f"Could not open categories.manage: {exc}")
        return []

    if _on_login_page(driver):
        _prog("Redirected to login on categories.manage.")
        if back and "products.update" in back:
            try:
                driver.get(back)
            except Exception:
                pass
        return []

    try:
        on_manage = driver.execute_script(
            r"""
            const u = (location.href || '').toLowerCase();
            return u.includes('categories.manage') ||
                /კატეგორ|categor/i.test(((document.body && document.body.innerText) || '').slice(0, 1500));
            """
        )
    except Exception:
        on_manage = False
    if not on_manage:
        _prog("Not on categories.manage — skip.")
        if back:
            try:
                driver.get(back)
            except Exception:
                pass
        return []

    raw_items: list[dict[str, Any]] = []

    def _scrape_dom() -> list:
        try:
            scraped = driver.execute_script(SCRAPE_CATEGORIES_MANAGE_SCRIPT) or {}
            batch = scraped.get("items") if isinstance(scraped, dict) else []
            return batch if isinstance(batch, list) else []
        except Exception:
            return []

    def _absorb(batch: list | None) -> None:
        nonlocal raw_items
        raw_items = _merge_category_item_lists(raw_items, batch)

    # --- 1) Primary: full catalog via AJAX (parent_id in JSON/HTML) ---
    _prog("Loading category tree via admin AJAX…")
    try:
        ajax = driver.execute_script(FETCH_FULL_CATEGORY_TREE_AJAX_SCRIPT) or {}
    except Exception as exc:
        ajax = {"error": str(exc), "items": []}
        _prog(f"AJAX catalog error: {exc}")
    if isinstance(ajax, dict):
        _absorb(ajax.get("items"))
        wp = int(ajax.get("with_parent") or 0)
        _prog(
            f"AJAX catalog: {ajax.get('count', 0)} cats · "
            f"{wp} with parent · roots_tried={ajax.get('roots_tried', 0)}"
        )

    # --- Fast path: tree already complete — skip UI expand / per-root waits ---
    skip_dom = _tree_good(raw_items)
    if skip_dom:
        _prog(
            f"Tree complete from AJAX ({len(raw_items)} cats, {_wp(raw_items)} subs) — skip UI expand"
        )
    else:
        # --- 2) Light expand (max 4 rounds) ---
        _prog("Expanding category rows…")
        for round_i in range(1, 5):
            try:
                info = driver.execute_script(EXPAND_ALL_CARETS_SCRIPT) or {}
            except Exception:
                info = {}
            clicks = int((info or {}).get("clicks") or 0)
            time.sleep(0.22 if clicks else 0.08)
            _absorb(_scrape_dom())
            n = len(raw_items)
            wp = _wp(raw_items)
            _prog(f"Expand {round_i}… clicks={clicks} total={n} with_parent={wp}")
            if clicks == 0 and round_i >= 2:
                break
            if _tree_good(raw_items) and clicks == 0:
                break

        # --- 3) Batch-fetch children only for parents still missing subs ---
        try:
            roots = driver.execute_script(LIST_ROOT_CATEGORY_IDS_SCRIPT) or {}
        except Exception:
            roots = {}
        root_ids = [
            str(x)
            for x in (roots.get("ids") if isinstance(roots, dict) else []) or []
            if str(x).isdigit()
        ]
        if not root_ids:
            root_ids = [
                str(x.get("id") or x.get("value") or "")
                for x in raw_items
                if isinstance(x, dict) and not str(x.get("parent_id") or "").strip()
            ]
        root_ids = [r for r in root_ids if r.isdigit()][:80]
        child_parents = {
            str(x.get("parent_id") or "")
            for x in raw_items
            if isinstance(x, dict) and str(x.get("parent_id") or "").strip()
        }
        need = [r for r in root_ids if r not in child_parents]
        if need and not _tree_good(raw_items):
            _prog(f"Batch-fetch children for {len(need)} parents…")
            # Expand a few missing parents so #cat_* appears (cheap)
            for cid in need[:12]:
                try:
                    driver.execute_script(EXPAND_ONE_CATEGORY_SCRIPT, cid)
                except Exception:
                    pass
            time.sleep(0.2)
            try:
                res = driver.execute_script(BATCH_FETCH_CHILDREN_SCRIPT, need) or {}
                if isinstance(res, dict):
                    _absorb(res.get("items"))
                    _prog(
                        f"Batch children: +{res.get('count', 0)} "
                        f"({res.get('parents_ok', 0)} parents filled)"
                    )
            except Exception as exc:
                _prog(f"Batch children error: {exc}")
                # Fallback: a few single fetches if batch fails
                for cid in need[:20]:
                    try:
                        res = driver.execute_script(FETCH_CHILDREN_FOR_PARENT_SCRIPT, cid)
                        if isinstance(res, dict):
                            _absorb(res.get("items"))
                    except Exception:
                        pass
            _absorb(_scrape_dom())

    # Final DOM pass only when hierarchy still incomplete
    if not _tree_good(raw_items):
        try:
            driver.execute_script(EXPAND_ALL_CARETS_SCRIPT)
        except Exception:
            pass
        time.sleep(0.15)
        _absorb(_scrape_dom())

    noise = re.compile(
        r"alexbranding|cart-power|cs-cart georgia|add-on market|addon market|storefront|"
        r"my add-ons|all add-ons|გადახდის|ტრანსპორტირ|^მაღაზია\s*:",
        re.I,
    )
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        lab = str(it.get("label") or "").strip()
        val = str(it.get("value") or it.get("id") or "").strip()
        if not lab or not val or noise.search(lab):
            continue
        if lab.isdigit() or not re.match(r"^\d+$", val):
            # allow non-numeric values rarely
            if not re.match(r"^\d+$", val) and lab.isdigit():
                continue
        if val in seen:
            continue
        seen.add(val)
        pid = str(it.get("parent_id") or "").strip()
        if pid in ("0", "false", "None"):
            pid = ""
        if pid == val:
            pid = ""
        clean.append(
            {
                "id": val,
                "value": val,
                "label": lab,
                "field_name": "",
                "selected": False,
                "path": str(it.get("path") or lab),
                "parent_id": pid,
                "level": it.get("level"),
                "source": str(it.get("source") or "categories.manage"),
            }
        )

    by_id = {str(c["id"]): c for c in clean}
    for c in clean:
        chain: list[str] = []
        cur: dict | None = c
        guard: set[str] = set()
        while cur and str(cur.get("id") or "") not in guard:
            guard.add(str(cur.get("id") or ""))
            chain.append(str(cur.get("label") or ""))
            pid = str(cur.get("parent_id") or "")
            cur = by_id.get(pid) if pid else None
        chain.reverse()
        if chain:
            c["path"] = " / ".join(chain)

    clean.sort(
        key=lambda c: (
            str(c.get("path") or c.get("label") or "").lower(),
            str(c.get("label") or "").lower(),
        )
    )

    n_subs = sum(1 for c in clean if c.get("parent_id"))
    n_parents_with_kids = len({c["parent_id"] for c in clean if c.get("parent_id")})
    if clean:
        _categories_manage_cache = [dict(x) for x in clean]
        _categories_manage_cache_key = cache_key
        try:
            _disk_save_categories(base, clean)
        except Exception:
            pass
        _prog(
            f"Categories ready: {len(clean)} total · "
            f"{n_subs} subs under {n_parents_with_kids} parents"
        )
    else:
        _prog("Categories manage: empty tree")

    if back and "categories.manage" not in (back or "").lower():
        try:
            _prog("Returning to product…")
            driver.get(back)
            time.sleep(0.45)
        except Exception:
            pass
    return clean


def enrich_category_options(
    driver,
    product_url: str | None = None,
    progress_cb=None,
) -> list[dict[str, Any]]:
    """
    Load full store category tree from admin Categories → Manage
    (dispatch=categories.manage), then merge product selected chips / picker extras.
    """

    def _prog(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    product_url = product_url or ""
    try:
        if not product_url:
            product_url = str(driver.current_url or "")
    except Exception:
        product_url = ""

    # --- 1) Full catalog from categories.manage (parents + subcategories) ---
    # force=False uses session cache after first good tree (huge speed win).
    # Re-scrape if cache is empty/flat.
    manage_items = scrape_categories_manage_tree(
        driver,
        progress_cb=progress_cb,
        force=False,
        return_url=product_url if "products.update" in (product_url or "") else None,
    )

    _ensure_on_product(driver, product_url)

    items: list[dict[str, Any]] = [dict(x) for x in manage_items]
    open_info: dict[str, Any] = {}

    def _merge_batch(batch: list | None) -> None:
        nonlocal items
        if not isinstance(batch, list):
            return
        by_v: dict[str, dict] = {
            str(x.get("value") or x.get("id") or ""): dict(x) for x in items if isinstance(x, dict)
        }
        for it in batch:
            if not isinstance(it, dict):
                continue
            lab = str(it.get("label") or "").strip()
            if not lab:
                continue
            v = str(it.get("value") or it.get("id") or lab)
            prev = by_v.get(v)
            if not prev or (str(prev.get("label") or "").isdigit() and not lab.isdigit()):
                by_v[v] = {
                    "id": v,
                    "value": v,
                    "label": lab,
                    "field_name": str(
                        it.get("field_name")
                        or open_info.get("selectName")
                        or (prev.get("field_name") if prev else "")
                        or ""
                    ),
                    "selected": bool(it.get("selected")),
                    "path": str(it.get("path") or (prev or {}).get("path") or lab),
                    "parent_id": str(it.get("parent_id") or (prev or {}).get("parent_id") or ""),
                    "level": it.get("level") if it.get("level") is not None else (prev or {}).get("level"),
                    "source": str(it.get("source") or (prev or {}).get("source") or "picker"),
                }
            else:
                if it.get("selected"):
                    prev["selected"] = True
                new_path = str(it.get("path") or "").strip()
                if new_path and (
                    not prev.get("path")
                    or prev.get("path") == prev.get("label")
                    or ("/" in new_path and "/" not in str(prev.get("path") or ""))
                ):
                    prev["path"] = new_path
                pid = str(it.get("parent_id") or "").strip()
                if pid and not prev.get("parent_id"):
                    prev["parent_id"] = pid
                if it.get("level") is not None and prev.get("level") is None:
                    prev["level"] = it.get("level")
                fn = str(it.get("field_name") or "").strip()
                if fn and not prev.get("field_name"):
                    prev["field_name"] = fn
        items = list(by_v.values())

    # Mark selected from product form
    try:
        sel_info = driver.execute_script(READ_PRODUCT_CATEGORY_SELECTION_SCRIPT) or {}
        if isinstance(sel_info, dict):
            open_info["selectName"] = sel_info.get("field_name") or ""
            for s in sel_info.get("selected") or []:
                if isinstance(s, dict):
                    s = dict(s)
                    s["selected"] = True
                    s["field_name"] = open_info.get("selectName") or ""
                    _merge_batch([s])
            # Stamp field_name on all manage items for fill
            fn = str(open_info.get("selectName") or "")
            if fn:
                for it in items:
                    if not it.get("field_name"):
                        it["field_name"] = fn
    except Exception:
        pass

    # If manage tree has real parent→child links, skip slow picker harvest
    if len(items) >= 8 and not _category_tree_is_flat(items):
        noise = re.compile(
            r"alexbranding|cart-power|cs-cart georgia|add-on market|addon market|storefront|"
            r"my add-ons|all add-ons|გადახდის|ტრანსპორტირ",
            re.I,
        )
        clean: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            lab = str(it.get("label") or "").strip()
            val = str(it.get("value") or it.get("id") or "").strip()
            if not lab or noise.search(lab):
                continue
            if lab.isdigit() and (not val or val == lab):
                continue
            key = val + "|" + lab
            if key in seen:
                continue
            seen.add(key)
            clean.append(
                {
                    "id": val or lab,
                    "value": val or lab,
                    "label": lab,
                    "field_name": str(it.get("field_name") or open_info.get("selectName") or ""),
                    "selected": bool(it.get("selected")),
                    "path": str(it.get("path") or lab),
                    "parent_id": str(it.get("parent_id") or ""),
                    "level": it.get("level"),
                    "source": str(it.get("source") or "categories.manage"),
                }
            )
        # Root-first sort by path for stable tree UI
        clean.sort(
            key=lambda c: (
                0 if c.get("selected") else 1,
                str(c.get("path") or c.get("label") or "").lower(),
            )
        )
        _ensure_on_product(driver, product_url)
        unsel = sum(1 for c in clean if not c.get("selected"))
        n_sub = sum(1 for c in clean if str(c.get("parent_id") or "").strip())
        _prog(
            f"Categories ready: {len(clean)} from manage "
            f"({n_sub} subcategories, {unsel} unselected)"
        )
        try:
            driver.set_script_timeout(180)
        except Exception:
            pass
        return clean

    # If manage is thin OR only mains, fall through to product picker harvest
    if len(items) >= 1:
        _prog(
            f"Manage had {len(items)} rows "
            f"{'(flat mains only) ' if _category_tree_is_flat(items) else ''}"
            f"— also loading product picker…"
        )

    # --- Fallback: product picker / AJAX (manage empty or still flat) ---
    if len(items) < 8 or _category_tree_is_flat(items):
        _prog("Loading categories via product picker (for missing subcategories)…")
    else:
        _prog("Categories manage thin — loading via product picker…")
    _ensure_on_product(driver, product_url)

    try:
        driver.set_script_timeout(45)
    except Exception:
        pass

    try:
        driver.execute_script(
            r"""
            ['content_general', 'content_detailed'].forEach(id => {
              const n = document.getElementById(id);
              if (n) { n.classList.remove('hidden', 'collapsed'); n.style.display = ''; }
            });
            document.querySelectorAll('a[href^="#content_"], a.cm-js, .nav-tabs a').forEach(a => {
              const t = ((a.innerText || a.textContent) || '').toLowerCase();
              const href = (a.getAttribute('href') || '');
              if (t.includes('general') || t.includes('ზოგად') || href.includes('content_general')) {
                try { a.click(); } catch (e) {}
              }
            });
            return true;
            """
        )
        time.sleep(0.3)
    except Exception:
        pass

    try:
        driver.execute_script(ENRICH_FEATURE_HOOK_SCRIPT)
    except Exception:
        pass

    # Keep any manage rows already in `items`; picker only adds / fills gaps
    def _unselected() -> int:
        return sum(1 for x in items if not x.get("selected"))

    def _total() -> int:
        return len(items)

    try:
        _prog("Opening Categories picker…")
        picker_open = driver.execute_script(OPEN_CATEGORY_PICKER_SCRIPT) or {}
        if isinstance(picker_open, dict):
            open_info.update(picker_open)
    except Exception as exc:
        open_info = {**open_info, "error": str(exc)}

    # Let CS-Cart fire its real picker XHR
    time.sleep(0.35)
    try:
        scraped = driver.execute_script(SCRAPE_CATEGORY_DROPDOWN_SCRIPT, False) or {}
        _merge_batch(scraped.get("items") if isinstance(scraped, dict) else None)
    except Exception:
        pass

    seeds: list[str] = []
    try:
        disc = driver.execute_script(CATEGORY_DISCOVER_SEEDS_SCRIPT) or {}
        if isinstance(disc, dict):
            raw = disc.get("seeds") or []
            seeds = [str(s) for s in raw if str(s).strip()]
            if disc.get("selectName") and not open_info.get("selectName"):
                open_info["selectName"] = disc.get("selectName")
            _prog(
                f"Category picker URL · {len(seeds)} sources · selected={disc.get('selectedCount', 0)}"
            )
    except Exception:
        seeds = []

    # Page through the best picker URL(s) until we have real unselected catalog entries
    t0 = time.perf_counter()
    budget_s = 50.0
    max_options = 3000
    best_seed: str | None = None
    best_seed_added = 0

    for seed_i, seed in enumerate(seeds[:6]):
        if time.perf_counter() - t0 > budget_s:
            break
        if _unselected() >= 80 and _total() >= 100:
            break
        empty_streak = 0
        seed_added_here = 0
        for page in range(1, 45):
            if time.perf_counter() - t0 > budget_s:
                break
            if _total() >= max_options:
                break
            _prog(f"Loading categories page {page}… ({_total()} found, {_unselected()} extra)")
            try:
                res = driver.execute_script(
                    CATEGORY_AJAX_ONE_PAGE_SCRIPT, seed, page, 120, ""
                ) or {}
            except Exception:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            if not isinstance(res, dict):
                empty_streak += 1
                continue
            if res.get("reason") == "login":
                break
            _merge_batch(res.get("items"))
            added = int(res.get("added") or 0)
            parsed = int(res.get("parsedCount") or 0)
            seed_added_here += added
            if added == 0 and parsed == 0:
                empty_streak += 1
                if empty_streak >= 2:
                    break
            else:
                empty_streak = 0
            # Stop this seed if total reported and we reached it
            total_hint = int(res.get("total") or 0)
            if total_hint and _total() >= total_hint and total_hint > 0:
                break
            if not res.get("more") and page > 1 and added == 0:
                break
        if seed_added_here > best_seed_added:
            best_seed_added = seed_added_here
            best_seed = seed
        # Prefer first seed that actually returns unselected options
        if _unselected() >= 40:
            break

    # Keyword probes only if catalog still thin (AJAX term — never type in UI)
    if _unselected() < 30 and best_seed and time.perf_counter() - t0 < budget_s:
        terms = [
            "ა", "ბ", "გ", "დ", "ე", "ვ", "ზ", "თ", "ი", "კ", "ლ", "მ", "ნ", "ო", "პ",
            "ჟ", "რ", "ს", "ტ", "უ", "ფ", "ქ", "ღ", "ყ", "შ", "ჩ", "ც", "ძ", "წ", "ჭ", "ხ", "ჯ", "ჰ",
            "გიტარ", "ელექტრო", "ბას", "მიკრ", "დრამ", "სინთ", "პიან",
            "guitar", "bass", "mic", "drum", "a", "e", "s", "g", "b", "m",
        ]
        for term in terms:
            if time.perf_counter() - t0 > budget_s or _unselected() >= 80:
                break
            _prog(f"Category search “{term}”… ({_total()} found)")
            try:
                res = driver.execute_script(
                    CATEGORY_AJAX_ONE_PAGE_SCRIPT, best_seed or seeds[0], 1, 100, term
                ) or {}
                if isinstance(res, dict):
                    _merge_batch(res.get("items"))
            except Exception:
                pass

    # Select2 open-list harvest (fills gaps the XHR parse missed)
    try:
        driver.execute_script(OPEN_CATEGORY_PICKER_SCRIPT)
    except Exception:
        pass
    last_count = _total()
    stable = 0
    scroll_rounds = 22 if _unselected() < 40 else 10
    for attempt in range(scroll_rounds):
        if time.perf_counter() - t0 > budget_s + 10:
            break
        _prog(f"Scrolling Categories list… ({_total()} found)")
        time.sleep(0.35)
        try:
            driver.execute_script(
                r"""
                document.querySelectorAll(
                  '.select2-container--open .select2-results__options, ' +
                  '.select2-container--open .select2-results, ' +
                  '.select2-dropdown .select2-results__options, ' +
                  '.select2-dropdown .select2-results, ' +
                  '.object-picker__results'
                ).forEach(b => {
                  try { b.scrollTop = b.scrollHeight; } catch (e) {}
                });
                try {
                  const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$);
                  const el = window.__ASF_CATEGORY_SELECT;
                  if ($ && el && $(el).data('select2')) {
                    const s2 = $(el).data('select2');
                    const page = (window.__ASF_CAT_PAGE = (window.__ASF_CAT_PAGE || 0) + 1);
                    try { s2.trigger('query', { term: '', page: page }); } catch (e) {}
                    try {
                      if (s2.dataAdapter && s2.dataAdapter.query) {
                        s2.dataAdapter.query({ term: '', page: page }, function () {});
                      }
                    } catch (e) {}
                  }
                } catch (e) {}
                return true;
                """
            )
        except Exception:
            pass
        try:
            res = driver.execute_script(SCRAPE_CATEGORY_DROPDOWN_SCRIPT, False) or {}
            _merge_batch(res.get("items") if isinstance(res, dict) else None)
        except Exception:
            pass
        # Absorb late net responses
        try:
            res2 = driver.execute_script(
                CATEGORY_AJAX_ONE_PAGE_SCRIPT,
                best_seed or (seeds[0] if seeds else ""),
                max(1, attempt + 1),
                100,
                "",
            ) or {}
            if isinstance(res2, dict):
                _merge_batch(res2.get("items"))
        except Exception:
            pass

        n = _total()
        if n == last_count and n > 0:
            stable += 1
        else:
            stable = 0
        last_count = n
        if stable >= 4 and _unselected() >= 15:
            break
        if stable >= 6 and n > 0:
            break
        if attempt in (4, 10) and _unselected() < 5:
            try:
                driver.execute_script(OPEN_CATEGORY_PICKER_SCRIPT)
            except Exception:
                pass

    try:
        res = driver.execute_script(SCRAPE_CATEGORY_DROPDOWN_SCRIPT, False) or {}
        _merge_batch(res.get("items") if isinstance(res, dict) else None)
    except Exception:
        pass
    try:
        driver.execute_script(SCRAPE_CATEGORY_DROPDOWN_SCRIPT, True)
    except Exception:
        pass
    try:
        driver.execute_script(
            r"""
            try {
              const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$);
              if ($ && $.fn && $.fn.select2) {
                $('select').each(function(){ try { if ($(this).data('select2')) $(this).select2('close'); } catch(e){} });
              }
            } catch(e) {}
            try { document.body.click(); } catch(e) {}
            window.__ASF_CAT_PAGE = 0;
            """
        )
    except Exception:
        pass

    noise = re.compile(
        r"alexbranding|cart-power|cs-cart georgia|add-on market|addon market|storefront|"
        r"my add-ons|all add-ons|გადახდის|ტრანსპორტირ",
        re.I,
    )
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        lab = str(it.get("label") or "").strip()
        val = str(it.get("value") or it.get("id") or "").strip()
        if not lab or noise.search(lab):
            continue
        if lab.isdigit() and (not val or val == lab):
            continue
        key = val + "|" + lab
        if key in seen:
            continue
        seen.add(key)
        clean.append(
            {
                "id": val or lab,
                "value": val or lab,
                "label": lab,
                "field_name": str(it.get("field_name") or open_info.get("selectName") or ""),
                "selected": bool(it.get("selected")),
                "path": str(it.get("path") or lab),
                "parent_id": str(it.get("parent_id") or ""),
                "level": it.get("level"),
            }
        )

    clean.sort(key=lambda c: (0 if c.get("selected") else 1, str(c.get("label") or "").lower()))
    _ensure_on_product(driver, product_url)
    unsel = sum(1 for c in clean if not c.get("selected"))
    _prog(f"Categories ready: {len(clean)} total ({unsel} not selected)")
    try:
        driver.set_script_timeout(180)
    except Exception:
        pass
    return clean



def scan_product_page(
    driver,
    product_url: str | None = None,
    progress_cb=None,
) -> dict[str, Any]:
    product_url = product_url or driver.current_url

    def _prog(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    # Read product name FIRST (before any tab interaction).
    title_probe = driver.execute_script(
        r"""
        const sels = [
            '#product_description_product',
            'input[name="product_data[product]"]',
            'input[name="product_data[product_description][product]"]'
        ];
        for (const s of sels) {
            const el = document.querySelector(s);
            if (el && el.value && String(el.value).trim()) return String(el.value).trim();
        }
        const h = document.querySelector('.mainbox-title, h1.mainbox-title, .mainbox-title span');
        return h ? (h.innerText || '').trim() : '';
        """
    )

    # Install network hook once (captures picker/feature AJAX for later parses)
    try:
        driver.execute_script(
            _FEATURE_JS_HELPERS + "return asfInstallNetHook();"
        )
    except Exception:
        pass

    _prog("Opening product tabs…")
    open_product_tabs(driver, product_url)

    # 1) Categories — memory/disk when hierarchy already known (no leave-product)
    _prog("Loading categories…")
    category_items = enrich_category_options(
        driver, product_url, progress_cb=progress_cb
    )

    # 2) Features — inject cache + batch AJAX + open only thin lists
    _prog("Loading feature options…")
    enrich_info = enrich_feature_select2_options(driver, product_url)

    # 3) Static scan of product fields (now with enriched option text)
    _prog("Reading product form…")
    result = driver.execute_script(SCAN_SCRIPT)
    if not isinstance(result, dict):
        result = {}

    # 4) Merge browser-side feature option cache (names from open dropdown / AJAX)
    try:
        cache = driver.execute_script(
            "return window.__ASF_FEATURE_OPTIONS || {};"
        ) or {}
    except Exception:
        cache = {}
    if isinstance(cache, dict) and cache:
        feats = result.get("available_features")
        if isinstance(feats, list):
            for feat in feats:
                if not isinstance(feat, dict):
                    continue
                fn = str(feat.get("field_name") or "")
                fid = str(feat.get("id") or "")
                keys = [fn, fid]
                cached: list = []
                for k in keys:
                    if k and k in cache and isinstance(cache[k], list):
                        cached = cache[k]
                        break
                # also try partial key match
                if not cached:
                    for ck, cv in cache.items():
                        if not isinstance(cv, list):
                            continue
                        if fn and (fn in str(ck) or str(ck) in fn):
                            cached = cv
                            break
                if not cached:
                    continue
                by_val: dict[str, dict] = {}
                for o in list(feat.get("options") or []):
                    if not isinstance(o, dict):
                        continue
                    by_val[str(o.get("value", ""))] = dict(o)
                for it in cached:
                    if not isinstance(it, dict):
                        continue
                    v = str(it.get("value", ""))
                    lab = str(it.get("label") or "").strip()
                    if not lab and not v:
                        continue
                    if v not in by_val:
                        by_val[v] = {"value": v, "label": lab or v, "selected": False}
                    else:
                        cur = by_val[v]
                        cur_lab = str(cur.get("label") or "").strip()
                        if lab and not lab.isdigit() and (not cur_lab or cur_lab == v or cur_lab.isdigit()):
                            cur["label"] = lab
                feat["options"] = list(by_val.values())
                # If we resolved any real names, hide pure-ID labels for the same feature
                human_opts = [
                    o for o in feat["options"]
                    if str(o.get("label") or "").strip()
                    and not str(o.get("label")).strip().isdigit()
                    and str(o.get("label")).strip() != "-ცარიელი-"
                ]
                if human_opts:
                    cleaned = []
                    for o in feat["options"]:
                        lab = str(o.get("label") or "").strip()
                        if lab.isdigit() and not o.get("selected"):
                            continue
                        cleaned.append(o)
                    # keep at least the human set
                    if cleaned:
                        feat["options"] = cleaned
                feat["selected_labels"] = [
                    str(o.get("label") or o.get("value") or "")
                    for o in feat["options"]
                    if o.get("selected")
                ]

    # Prefer live product-category picker results over scan noise
    # (scan could still pick wrong admin lists if any remain)
    if category_items:
        # Always prefer enriched picker list when non-empty — it is the real store categories
        result["available_category_options"] = category_items
        result["available_categories"] = [
            c.get("label") for c in category_items if c.get("selected") and c.get("label")
        ]
    else:
        # Filter any leftover admin noise from static scan
        opts = result.get("available_category_options") or []
        if isinstance(opts, list):
            noise = re.compile(
                r"alexbranding|cart-power|cs-cart|add-on|addon market|storefront|"
                r"გადახდის|ტრანსპორტირ",
                re.I,
            )
            result["available_category_options"] = [
                o for o in opts
                if isinstance(o, dict) and not noise.search(str(o.get("label") or ""))
            ]

    result["enrich_features"] = enrich_info
    result["enrich_categories_count"] = len(category_items)

    if title_probe and not str(result.get("product_name") or "").strip():
        result["product_name"] = title_probe
    elif title_probe:
        result["product_name"] = title_probe

    result["scan_url"] = driver.current_url
    result["still_on_product_update"] = _on_product_update(driver)
    return result



# After Fill, feature Select2 often keeps "..." — reassert option text + rendered label ONLY.
# Never re-trigger change (that wipes CS-Cart object-picker selection display back to "...").
FEATURE_DISPLAY_REPAIR_SCRIPT = r"""
const stash = Array.isArray(window.__ASF_FEATURE_FILL) ? window.__ASF_FEATURE_FILL : [];
if (!stash.length) return { patched: 0 };

function isBad(t) {
    const s = String(t == null ? '' : t).replace(/[\u00d7]/g, '').trim();
    return !s || s === '...' || s === '\u2026' || s === '-';
}

function findSelect(entry) {
    if (entry && entry.name) {
        const hit = Array.from(document.querySelectorAll('select')).find(
            s => (s.getAttribute('name') || '') === entry.name
        );
        if (hit) return hit;
    }
    if (entry && entry.id) {
        const el = document.getElementById(entry.id);
        if (el && el.tagName === 'SELECT') return el;
    }
    return null;
}

function rootsFor(el) {
    const roots = [];
    const tryData = ($) => {
        try {
            if ($ && $(el).data && $(el).data('select2')) {
                const s2 = $(el).data('select2');
                if (s2.$selection && s2.$selection[0]) roots.push(s2.$selection[0]);
                if (s2.$container && s2.$container[0]) roots.push(s2.$container[0]);
            }
        } catch (e) {}
    };
    tryData(window.jQuery || window.$ || null);
    tryData((window.Tygh && Tygh.$) || null);
    try {
        const eid = el.id || '';
        if (eid) {
            const cont = document.getElementById('select2-' + eid + '-container');
            if (cont) {
                roots.push(cont);
                const box = cont.closest && cont.closest('.select2-container, .select2');
                if (box) roots.push(box);
            }
        }
    } catch (e) {}
    try {
        const sib = el.nextElementSibling;
        if (sib && /select2|object-picker/.test(sib.className || '')) roots.push(sib);
    } catch (e) {}
    try {
        const wrap = el.closest(
            '.object-picker, .cm-object-picker, .cm-field-container, .controls, .control-group, .ty-control-group, tr'
        );
        if (wrap) {
            const selects = wrap.querySelectorAll('select');
            if (selects.length === 1 && selects[0] === el) roots.push(wrap);
        }
    } catch (e) {}
    return Array.from(new Set(roots.filter(Boolean)));
}

function writeLabel(node, display) {
    if (!node || !display || isBad(display)) return false;
    try { node.setAttribute('title', display); } catch (e) {}
    const cd = node.querySelector && node.querySelector('.select2-selection__choice__display');
    if (cd) { cd.textContent = display; return true; }
    const clear = node.querySelector && node.querySelector('.select2-selection__clear');
    const remove = node.querySelector && node.querySelector('.select2-selection__choice__remove');
    if (clear) {
        node.innerHTML = '';
        node.appendChild(clear);
        node.appendChild(document.createTextNode(display));
        return true;
    }
    if (remove) {
        node.innerHTML = '';
        node.appendChild(remove);
        node.appendChild(document.createTextNode(display));
        return true;
    }
    if (!(node.querySelector && node.querySelector('input'))) {
        node.textContent = display;
        return true;
    }
    return false;
}

let patched = 0;
stash.forEach(entry => {
    const el = findSelect(entry);
    if (!el) return;
    const ids = (entry.ids || []).map(String).filter(Boolean);
    const texts = entry.texts || [];
    if (!ids.length) return;

    ids.forEach((id, i) => {
        const display = texts[i] || texts[0] || '';
        if (!display || isBad(display)) return;
        let opt = Array.from(el.options || []).find(o => String(o.value) === String(id));
        if (!opt) {
            try { opt = new Option(display, id, true, true); }
            catch (e) {
                opt = document.createElement('option');
                opt.value = id; opt.text = display; opt.selected = true;
            }
            el.appendChild(opt);
        } else {
            opt.textContent = display;
            opt.selected = true;
            try { opt.setAttribute('data-asf-label', display); } catch (e) {}
        }
    });

    try {
        if (el.multiple) {
            Array.from(el.options || []).forEach(o => {
                o.selected = ids.indexOf(String(o.value)) >= 0;
            });
        } else if (ids[0]) {
            Array.from(el.options || []).forEach(o => {
                o.selected = String(o.value) === String(ids[0]);
            });
            try { el.value = ids[0]; } catch (e) {}
        }
    } catch (e) {}

    const display0 = texts[0] || '';
    if (!display0 || isBad(display0)) return;
    rootsFor(el).forEach(root => {
        if (!root || !root.querySelectorAll) return;
        root.querySelectorAll(
            '.select2-selection__rendered, .select2-selection__choice, ' +
            '.object-picker__selection-text, .cm-object-picker-selected'
        ).forEach(node => {
            if (node.tagName === 'INPUT') return;
            if (writeLabel(node, display0)) patched++;
        });
    });
});
return { patched: patched, entries: stash.length };
"""


# After Fill, Select2/object-picker may re-render chips as "..." — rewrite display labels.
CATEGORY_CHIP_REPAIR_SCRIPT = r"""
const items = Array.isArray(window.__ASF_CAT_FILL_ITEMS) ? window.__ASF_CAT_FILL_ITEMS : [];
if (!items.length) return { patched: 0 };

function isProductCategorySelect(el) {
    if (!el || el.tagName !== 'SELECT') return false;
    const n = (el.getAttribute('name') || '').toLowerCase();
    const id = (el.id || '').toLowerCase();
    if (n.includes('product_data') && n.includes('category')) return true;
    if (n.includes('category_ids') && !n.includes('company') && !n.includes('storefront')) return true;
    if (id.includes('product_categor')) return true;
    const group = el.closest('.control-group, .ty-control-group, .form-group, tr');
    if (group) {
        const lab = group.querySelector('label, .control-label');
        const t = ((lab && (lab.innerText || lab.textContent)) || '').toLowerCase();
        if (t.includes('კატეგორ') || (t.includes('categor') && !t.includes('feature'))) return true;
    }
    return false;
}

function writeChip(chip, lab) {
    try { chip.setAttribute('title', lab); } catch (e) {}
    const display = chip.querySelector('.select2-selection__choice__display');
    if (display) { display.textContent = lab; return; }
    const remove = chip.querySelector('.select2-selection__choice__remove');
    if (remove) {
        chip.innerHTML = '';
        chip.appendChild(remove);
        chip.appendChild(document.createTextNode(lab));
    } else {
        chip.textContent = lab;
    }
}

const $ = window.jQuery || window.$ || (window.Tygh && Tygh.$) || null;
let patched = 0;
const selects = Array.from(document.querySelectorAll('select')).filter(isProductCategorySelect);
selects.forEach(sel => {
    const ids = items.map(it => String(it.id));
    items.forEach(it => {
        let opt = Array.from(sel.options || []).find(o => String(o.value) === String(it.id));
        if (!opt) {
            try { opt = new Option(it.label, it.id, true, true); }
            catch (e) {
                opt = document.createElement('option');
                opt.value = it.id; opt.text = it.label; opt.selected = true;
            }
            sel.appendChild(opt);
        } else {
            opt.textContent = it.label;
            opt.selected = true;
            try { opt.setAttribute('data-asf-label', it.label); } catch (e) {}
        }
    });
    try {
        Array.from(sel.options || []).forEach(o => {
            o.selected = ids.indexOf(String(o.value)) >= 0;
        });
    } catch (e) {}
    try {
        if ($ && $(sel).length) {
            if (sel.multiple) $(sel).val(ids).trigger('change');
            else if (ids[0]) $(sel).val(ids[0]).trigger('change');
        }
    } catch (e) {}
    const roots = [];
    try {
        if ($ && $(sel).data('select2')) {
            const s2 = $(sel).data('select2');
            if (s2.$selection && s2.$selection[0]) roots.push(s2.$selection[0]);
            if (s2.$container && s2.$container[0]) roots.push(s2.$container[0]);
        }
    } catch (e) {}
    if (sel.nextElementSibling) roots.push(sel.nextElementSibling);
    const g = sel.closest('.control-group, .controls, .object-picker, td') || sel.parentElement;
    if (g) roots.push(g);
    roots.forEach(root => {
        if (!root || !root.querySelectorAll) return;
        const chips = Array.from(root.querySelectorAll('.select2-selection__choice'));
        chips.forEach((chip, idx) => {
            const raw = (chip.getAttribute('title') || chip.textContent || '')
                .replace(/×/g, '').trim();
            let it = items.find(x => String(raw) === String(x.id) || raw === x.label);
            if (!it && (raw === '...' || raw === '…' || /^\d+$/.test(raw) || !raw)) {
                it = items[idx] || items[0];
            }
            if (it) { writeChip(chip, it.label); patched++; }
        });
    });
});
return { patched: patched, items: items.length };
"""


def apply_product_fill(driver, data: dict[str, Any], product_url: str | None = None) -> dict[str, Any]:
    product_url = product_url or driver.current_url
    open_product_tabs(driver, product_url)
    time.sleep(0.6)
    _ensure_on_product(driver, product_url)
    payload = json.dumps(data, ensure_ascii=False)
    script = FILL_SCRIPT_TEMPLATE.replace("__PAYLOAD__", payload)
    result = driver.execute_script(script)
    # Category chips + feature Select2 (brand) often flicker to "..."; re-assert labels.
    try:
        time.sleep(0.45)
        repair = driver.execute_script(CATEGORY_CHIP_REPAIR_SCRIPT)
        feat_repair = driver.execute_script(FEATURE_DISPLAY_REPAIR_SCRIPT)
        if isinstance(result, dict):
            if isinstance(repair, dict):
                result["category_chip_repair"] = repair
            if isinstance(feat_repair, dict):
                result["feature_display_repair"] = feat_repair
        time.sleep(0.35)
        driver.execute_script(CATEGORY_CHIP_REPAIR_SCRIPT)
        driver.execute_script(FEATURE_DISPLAY_REPAIR_SCRIPT)
    except Exception:
        pass
    _ensure_on_product(driver, product_url)
    return result if isinstance(result, dict) else {}


CLICK_PRODUCT_SAVE_SCRIPT = r"""
// Click Save / შენახვა ONCE on the open product update form.
function textOf(el) {
    return ((el && (el.innerText || el.textContent || el.value || el.getAttribute('title'))) || '')
        .replace(/\s+/g, ' ').trim();
}
function isVisible(el) {
    if (!el) return false;
    try {
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
    } catch (e) { return false; }
}
function scoreSave(el) {
    if (!el || !isVisible(el)) return -1;
    const t = textOf(el).toLowerCase();
    const name = ((el.getAttribute('name') || '') + ' ' + (el.id || '') + ' ' +
        (el.className || '') + ' ' + (el.getAttribute('href') || '')).toLowerCase();
    // Hard reject
    if (/delete|remove|cancel|clone|preview|back|გაუქმ|წაშლ|კლონ|წინასწარ|dropdown/.test(t + ' ' + name)) return -1;
    if (/dispatch\[products\.delete|products\.delete|m_delete|products\.clone/i.test(name)) return -1;
    let s = 0;
    // Exact CS-Cart product save submit
    if (/dispatch\[products\.update\]/i.test(name)) s += 100;
    if (/products\.update/i.test(name) && /dispatch/i.test(name)) s += 40;
    if (el.type === 'submit') s += 20;
    if (/შენახვა|შეინახე|^save$|save and close|save changes|განახლება|^update$/i.test(t)) s += 50;
    if (t.includes('save') || t.includes('შენახვ')) s += 30;
    if (el.closest('#actions_panel, .actions-panel, .cm-product-save-buttons, .btn-bar, .buttons-container, .mainbox-title, .content-btns')) s += 15;
    if (el.classList && (el.classList.contains('btn-primary') || el.classList.contains('cm-submit'))) s += 10;
    return s;
}
function tryClick(el) {
    if (!el) return false;
    try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    try { el.focus(); } catch (e) {}
    try { el.click(); return true; } catch (e) {
        try {
            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            return true;
        } catch (e2) { return false; }
    }
}

// 1) Prefer canonical product save input
const prefer = document.querySelector(
    'input[type="submit"][name="dispatch[products.update]"], ' +
    'button[type="submit"][name="dispatch[products.update]"], ' +
    'input[name="dispatch[products.update]"], ' +
    'button[name="dispatch[products.update]"]'
);
if (prefer && isVisible(prefer) && tryClick(prefer)) {
    return { ok: true, how: 'dispatch_products_update', text: textOf(prefer).slice(0, 80) };
}

// 2) Score all buttons
const candidates = Array.from(document.querySelectorAll(
    'input[type="submit"], button[type="submit"], button.cm-submit, a.cm-submit, ' +
    'input.cm-submit, .btn-primary, .btn, button, a.btn, ' +
    '#actions_panel a, #actions_panel button, #actions_panel input, ' +
    '.cm-product-save-buttons a, .cm-product-save-buttons button, .cm-product-save-buttons input'
));
let best = null, bestS = 0;
candidates.forEach(el => {
    const s = scoreSave(el);
    if (s > bestS) { bestS = s; best = el; }
});
if (best && bestS >= 25 && tryClick(best)) {
    return { ok: true, how: 'scored', text: textOf(best).slice(0, 80), score: bestS };
}

// 3) Submit product form directly (last resort)
const form = document.querySelector(
    'form[name="product_update_form"], form#product_update_form, ' +
    'form[action*="products.update"], form.cm-processed-form'
) || document.querySelector('form[method="post"]');
if (form) {
    try {
        // Ensure dispatch field for save
        let hidden = form.querySelector('input[name="dispatch[products.update]"]');
        if (!hidden) {
            hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = 'dispatch[products.update]';
            hidden.value = '1';
            form.appendChild(hidden);
        }
        if (typeof form.requestSubmit === 'function') form.requestSubmit();
        else form.submit();
        return { ok: true, how: 'form_submit' };
    } catch (e) {
        return { ok: false, reason: 'form_submit_failed:' + String(e) };
    }
}
return { ok: false, reason: 'save_button_not_found', bestScore: bestS };
"""


def click_product_save(driver, *, product_url: str | None = None, wait_s: float = 2.2) -> dict[str, Any]:
    """
    Click CS-Cart Save on the current product update page.
    """
    _ensure_on_product(driver, product_url)
    try:
        if not _on_product_update(driver):
            return {"ok": False, "reason": "not_on_product_update"}
    except Exception:
        pass
    try:
        info = driver.execute_script(CLICK_PRODUCT_SAVE_SCRIPT) or {}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    if not isinstance(info, dict):
        info = {"ok": False, "reason": "bad_result"}
    if info.get("ok"):
        time.sleep(max(1.2, float(wait_s)))
        # Wait for save processing / notification / still on edit
        try:
            for _ in range(8):
                time.sleep(0.35)
                state = driver.execute_script(
                    r"""
                    const note = document.querySelector(
                      '.notification-content, .alert-success, .cm-notification-content, ' +
                      '.alert-info, .notification-content-success'
                    );
                    const txt = ((note && (note.innerText || note.textContent)) || '').toLowerCase();
                    const body = ((document.body && document.body.innerText) || '').slice(0, 400).toLowerCase();
                    const hasOk = /success|შენახ|განახლ|saved|updated|წარმატ/.test(txt + ' ' + body);
                    return {
                      url: location.href || '',
                      hasNotification: !!note,
                      successHint: hasOk,
                      onUpdate: (location.href || '').toLowerCase().includes('products.update')
                    };
                    """
                ) or {}
                if state.get("successHint") or state.get("hasNotification"):
                    info["saved_signal"] = state
                    break
                if not state.get("onUpdate"):
                    # left update after save is also a signal
                    info["saved_signal"] = state
                    break
        except Exception:
            pass
    return info


VERIFY_PRODUCT_DESCRIPTION_SCRIPT = r"""
// Read back filled product fields to confirm fill landed in the DOM.
function val(sels) {
    for (const s of sels) {
        const el = document.querySelector(s);
        if (!el) continue;
        if ('value' in el && String(el.value || '').trim()) return String(el.value).trim();
        const t = (el.innerText || el.textContent || '').trim();
        if (t) return t;
    }
    // tinymce
    try {
        if (window.tinymce) {
            const ed = tinymce.get('elm_product_full_descr') || tinymce.get('elm_full_descr') ||
                tinymce.editors && tinymce.editors[0];
            if (ed) {
                const c = (ed.getContent({ format: 'text' }) || '').trim();
                if (c) return c;
            }
        }
    } catch (e) {}
    return '';
}
const name = val([
    '#product_description_product',
    'input[name="product_data[product]"]',
    'input[name="product_data[product_description][product]"]'
]);
const descr = val([
    'textarea#elm_product_full_descr',
    'textarea#elm_full_descr',
    'textarea[name="product_data[full_description]"]',
    'textarea[name="product_data[product_description][full_description]"]'
]);
return {
    name: name.slice(0, 120),
    description_len: descr.length,
    description_preview: descr.slice(0, 80),
    ok: (name.length >= 2) || (descr.length >= 20)
};
"""


def verify_product_form_filled(driver) -> dict[str, Any]:
    try:
        info = driver.execute_script(VERIFY_PRODUCT_DESCRIPTION_SCRIPT) or {}
        return info if isinstance(info, dict) else {"ok": False}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


# ---------- Bulk: product list page (products.manage / filtered lists) ----------

SCAN_PRODUCT_LIST_SCRIPT = r"""
const out = [];
const seen = new Set();
const baseScript = (location.href || '').split('?')[0] || (location.origin + location.pathname);

function clean(s) {
    return String(s || '').replace(/\s+/g, ' ').trim();
}

function pushRow(id, name, checked, rawHref) {
    id = String(id || '').trim();
    if (!id || !/^\d+$/.test(id)) return;
    if (seen.has(id)) return;
    seen.add(id);
    name = clean(name) || ('Product #' + id);
    let edit = '';
    if (rawHref && /product_id=\d+/i.test(rawHref) && /products\.update/i.test(rawHref)) {
        try {
            edit = new URL(rawHref, location.href).href;
        } catch (e) { edit = ''; }
    }
    if (!edit) {
        edit = baseScript + '?dispatch=products.update&product_id=' + encodeURIComponent(id);
    }
    out.push({
        product_id: id,
        name: name,
        edit_url: edit,
        checked: !!checked
    });
}

// Checkboxes: product_ids[] or name containing product_id value
document.querySelectorAll(
    'input[type="checkbox"][name*="product_ids"], ' +
    'input[type="checkbox"][name*="product_id"], ' +
    'input[type="checkbox"].cm-item, ' +
    'input.cm-check-items[type="checkbox"], ' +
    'table input[type="checkbox"][value]'
).forEach(cb => {
    const val = String(cb.value || '').trim();
    if (!/^\d+$/.test(val)) return;
    // Skip "check all" master boxes
    if (cb.classList.contains('cm-check-items') && !cb.name) return;
    if ((cb.getAttribute('name') || '') === 'check_all') return;

    let name = '';
    const tr = cb.closest('tr, .ty-table__item, .cm-row-item, [id*="product"]');
    if (tr) {
        const link = tr.querySelector(
            'a[href*="products.update"][href*="product_id="], a[href*="product_id="][href*="update"]'
        );
        if (link) name = clean(link.innerText || link.textContent || '');
        if (!name) {
            const strong = tr.querySelector('a.row-status, .product-title, td a');
            if (strong) name = clean(strong.innerText || strong.textContent || '');
        }
    }
    pushRow(val, name, !!cb.checked, '');
});

// Links to product edit even without checkboxes
document.querySelectorAll('a[href*="product_id="]').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (!/products\.update/i.test(href) && !/dispatch=products\.update/i.test(href)) {
        // still allow product_id if look like update in admin
        if (!/update/i.test(href)) return;
    }
    const m = href.match(/product_id=(\d+)/i);
    if (!m) return;
    const name = clean(a.innerText || a.textContent || a.getAttribute('title') || '');
    // avoid tiny nav/chip links
    if (name.length < 2 && !a.closest('table, .table, .ty-table')) return;
    pushRow(m[1], name, false, href);
});

// Nested rows already collected by checkbox may miss names — enrich from links
document.querySelectorAll('a[href*="products.update"][href*="product_id="]').forEach(a => {
    const href = a.getAttribute('href') || '';
    const m = href.match(/product_id=(\d+)/i);
    if (!m) return;
    const id = m[1];
    const name = clean(a.innerText || a.textContent || '');
    const existing = out.find(r => r.product_id === id);
    if (existing) {
        if (name && (existing.name.startsWith('Product #') || existing.name.length < name.length)) {
            existing.name = name;
        }
        if (href) {
            try { existing.edit_url = new URL(href, location.href).href; } catch (e) {}
        }
        // if checkbox in same row, already set checked
        return;
    }
    pushRow(id, name, false, href);
});

// Re-mark checked from DOM if we found rows via links first
document.querySelectorAll('input[type="checkbox"][value]').forEach(cb => {
    const val = String(cb.value || '').trim();
    if (!/^\d+$/.test(val) || !cb.checked) return;
    const row = out.find(r => r.product_id === val);
    if (row) row.checked = true;
});

const pageUrl = location.href || '';
const isListLike =
    /dispatch=products\.manage/i.test(pageUrl) ||
    /products\.manage/i.test(pageUrl) ||
    out.length > 0;

return {
    ok: isListLike || out.length > 0,
    page_url: pageUrl,
    count: out.length,
    selected_count: out.filter(r => r.checked).length,
    products: out
};
"""


def scan_product_list_page(driver) -> dict[str, Any]:
    """
    Read product rows from CS-Cart products list (products.manage / filtered).
    Returns { products: [{product_id, name, edit_url, checked}], ... }.
    """
    # Prefer a tab that looks like a product list
    try:
        original = driver.current_window_handle
    except Exception:
        original = None
    list_found = False
    try:
        for handle in driver.window_handles:
            try:
                driver.switch_to.window(handle)
                url = (driver.current_url or "").lower()
                if "products.manage" in url or "dispatch=products.manage" in url:
                    list_found = True
                    break
            except Exception:
                continue
        if not list_found and original:
            try:
                driver.switch_to.window(original)
            except Exception:
                pass
    except Exception:
        pass

    try:
        result = driver.execute_script(SCAN_PRODUCT_LIST_SCRIPT) or {}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "products": [],
            "count": 0,
            "selected_count": 0,
            "page_url": "",
        }
    if not isinstance(result, dict):
        result = {}
    products = result.get("products") if isinstance(result.get("products"), list) else []
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("product_id") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        edit = str(p.get("edit_url") or "").strip()
        if not edit or "products.update" not in edit:
            try:
                page = driver.current_url or "https://acoustic.ge/aco_st_admin.php"
                base = page.split("?")[0]
                edit = f"{base}?dispatch=products.update&product_id={pid}"
            except Exception:
                edit = (
                    f"https://acoustic.ge/aco_st_admin.php"
                    f"?dispatch=products.update&product_id={pid}"
                )
        clean.append(
            {
                "product_id": pid,
                "name": str(p.get("name") or f"Product #{pid}").strip(),
                "edit_url": edit,
                "checked": bool(p.get("checked")),
            }
        )
    return {
        "ok": bool(clean) or bool(result.get("ok")),
        "page_url": str(result.get("page_url") or getattr(driver, "current_url", "") or ""),
        "count": len(clean),
        "selected_count": sum(1 for p in clean if p.get("checked")),
        "products": clean,
        "error": str(result.get("error") or ""),
    }


def open_product_edit(driver, product_url: str, *, timeout_s: float = 25.0) -> str:
    """
    Navigate current Chrome tab to products.update&product_id=…
    Returns final URL. Raises RuntimeError on login / missing form.
    """
    product_url = str(product_url or "").strip()
    if "products.update" not in product_url or "product_id=" not in product_url:
        raise RuntimeError(f"Not a product edit URL: {product_url}")

    if _on_login_page(driver):
        raise RuntimeError(
            "Chrome is on the admin login page. Log in in the debug Chrome window first."
        )

    try:
        if (driver.current_url or "").rstrip("/") != product_url.rstrip("/"):
            driver.get(product_url)
    except Exception as exc:
        raise RuntimeError(f"Could not open product page: {exc}") from exc

    deadline = time.time() + timeout_s
    last_url = ""
    while time.time() < deadline:
        try:
            last_url = driver.current_url or ""
        except Exception:
            last_url = ""
        if _on_login_page(driver):
            raise RuntimeError(
                "Redirected to login while opening product. Session expired — log in again."
            )
        if "dispatch=products.update" in last_url or "products.update" in last_url:
            # Name field present?
            try:
                has_name = driver.execute_script(
                    r"""
                    const sels = [
                      '#product_description_product',
                      'input[name="product_data[product]"]',
                      'input[name="product_data[product_description][product]"]'
                    ];
                    for (const s of sels) {
                      if (document.querySelector(s)) return true;
                    }
                    return !!(document.querySelector('.mainbox-title, h1.mainbox-title'));
                    """
                )
            except Exception:
                has_name = False
            if has_name:
                time.sleep(0.35)
                return last_url
        time.sleep(0.35)

    raise RuntimeError(
        f"Timed out waiting for product edit form.\nURL: {last_url or product_url}"
    )
