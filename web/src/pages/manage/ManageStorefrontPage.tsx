import { useEffect, useState, type ReactNode } from "react";
import { Link, useBlocker } from "react-router-dom";
import { ManageShell } from "@/features/restaurant/components/ManageShell";
import { ImagePicker, useUploadsInFlight } from "@/features/restaurant/components/ImagePicker";
import { BannerFraming, DEFAULT_FRAMING } from "@/features/restaurant/components/BannerFraming";
import { Loading, StatePage } from "@/components/common/Feedback";
import { SettingsCard } from "@/features/restaurant/components/SettingsCard";
import { HomeBanner, CategoryShortcuts, heroPhoto } from "@/features/storefront/components/HomePresentation";
import { attachFont, contrastResults, FONT_PAIRS, mapPins, PALETTES, themeVariables, type FontPair, type MapPins, type Storefront, type StorefrontTheme } from "@/features/storefront/theme";
import { markerSvg } from "@/features/storefront/components/DeliveryTracking";
import { isMapStyleKey, mapStyle, MAP_STYLES } from "@/features/storefront/mapStyles";
import { useStorefrontSettingsQuery, useSaveStorefrontThemeMutation, useSaveStorefrontBannersMutation, useSaveStorefrontCollectionsMutation, useSaveStorefrontShortcutsMutation, useSaveStorefrontMapMutation, type StorefrontSettings, type BannerDraft, type CategoryDraft, type CollectionDraft, type ShortcutDraft } from "@/features/restaurant/storefrontApi";
import { errorMessage } from "@/services/apiClient";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
let draftSequence = 0;
const newId = () => `new-${Date.now()}-${draftSequence++}`;
function move<T>(list: T[], index: number, step: number): T[] {
  const next = [...list];
  const source = next[index], target = next[index + step];
  if (source === undefined || target === undefined) return list;
  next[index] = target; next[index + step] = source;
  return next;
}
function localDate(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

export function ManageStorefrontPage() {
  const query = useStorefrontSettingsQuery();
  return <ManageShell>{query.error ? <StatePage title="Storefront unavailable">{errorMessage(query.error)}</StatePage> : query.data ? <StorefrontEditor initial={query.data} /> : <Loading />}</ManageShell>;
}

/** Drafts are separate from the query cache: saving a category must not
 * erase the banner the manager is still writing. Each successful save
 * adopts only that section's returned values and leaves the others alone. */
function StorefrontEditor({ initial }: { initial: StorefrontSettings }) {
  const [saved, setSaved] = useState(initial);
  const [banners, setBanners] = useState(initial.banners);
  const [interval, setInterval] = useState(initial.banner_interval_ms);
  const categories = initial.categories;
  const [shortcuts, setShortcuts] = useState(initial.shortcuts);
  const [adding, setAdding] = useState("");
  const [collections, setCollections] = useState(initial.collections);
  const [theme, setTheme] = useState(initial.theme);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploads, trackUpload] = useUploadsInFlight();
  const [saveTheme] = useSaveStorefrontThemeMutation();
  const [saveBanners] = useSaveStorefrontBannersMutation();
  const [saveShortcuts] = useSaveStorefrontShortcutsMutation();
  const [saveMap] = useSaveStorefrontMapMutation();
  const [mapStyle, setMapStyle] = useState(initial.map_style_key ?? "standard");
  const [mapPinsThemed, setMapPinsThemed] = useState(initial.map_pins_themed);
  const [saveCollections] = useSaveStorefrontCollectionsMutation();
  const dirty = !same(banners, saved.banners) || interval !== saved.banner_interval_ms || !same(shortcuts, saved.shortcuts) || mapStyle !== (saved.map_style_key ?? "standard") || mapPinsThemed !== saved.map_pins_themed || !same(collections, saved.collections) || !same(theme, saved.theme);
  const blocker = useBlocker(dirty || uploads > 0);
  useEffect(() => {
    if (!dirty && !uploads) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, uploads]);
  useEffect(() => attachFont(theme?.font_pair ?? "default"), [theme?.font_pair]);
  const off = !!busy || uploads > 0;
  async function save(section: string, action: () => Promise<void>) {
    setBusy(section); setError(null); setMessage(null);
    try { await action(); setMessage(`${section} saved.`); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(null); }
  }
  function banner(index: number, changes: Partial<BannerDraft>) { setBanners((rows) => rows.map((row, n) => n === index ? { ...row, ...changes } : row)); }
  function shortcut(index: number, changes: Partial<ShortcutDraft>) { setShortcuts((rows) => rows.map((row, n) => n === index ? { ...row, ...changes } : row)); }
  // A shortcut offers its category's items and its subcategories' items:
  // "Food" may point at a burger filed under Food / Burgers.
  const family = (typeId: string): CategoryDraft[] => categories.filter((c) => c.id === typeId || c.parent_id === typeId);
  const familyItems = (typeId: string) => family(typeId).flatMap((c) => c.items);
  const categoryName = (c: CategoryDraft) => c.parent_id ? `${categories.find((p) => p.id === c.parent_id)?.name ?? ""} / ${c.name}` : c.name;
  function addShortcut(typeId: string) {
    const category = categories.find((c) => c.id === typeId);
    if (!category) return;
    setShortcuts((rows) => [...rows, { id: newId(), item_type_id: typeId, label: category.name.slice(0, 40), image_path: null, image_url: null, is_active: true, item_ids: familyItems(typeId).map((i) => i.id).slice(0, 40) }]);
  }
  function collection(index: number, changes: Partial<CollectionDraft>) { setCollections((rows) => rows.map((row, n) => n === index ? { ...row, ...changes } : row)); }
  // What a customer can actually order. The storefront drops a collection's
  // items that are on no meal period, so the picker and preview say so too.
  const served = new Set(initial.menu.meals.flatMap((m) => m.sections.flatMap((s) => [...s.items, ...s.groups.flatMap((g) => g.items)])).map((i) => i.id));
  const itemNames = new Map(categories.flatMap((c) => c.items).map((i) => [i.id, i.name] as const));
  const emptyShown = collections.filter((c) => c.is_active && c.item_ids.length === 0);
  const emptyShortcuts = shortcuts.filter((s) => s.is_active && s.item_ids.length === 0);
  const chosenTheme = theme ?? PALETTES[0]!.theme;
  const checks = contrastResults(chosenTheme);
  const preview: Storefront = { theme, logo_url: initial.logo_url, banner_interval_ms: interval,
    banners: banners.filter((b) => b.is_active && b.image_url && (!b.starts_at || Date.parse(b.starts_at) <= Date.now()) && (!b.ends_at || Date.parse(b.ends_at) > Date.now())),
    categories: Object.fromEntries(categories.map((c, order) => [c.id, { image_url: c.image_url, show_in_shortcuts: c.show_in_shortcuts, sort_order: order }])), collections,
    shortcuts: shortcuts.filter((s) => s.is_active && s.label.trim() && s.item_ids.some((id) => served.has(id))).map((s) => ({
      id: s.id, item_type_id: s.item_type_id, label: s.label.trim(), item_ids: s.item_ids.filter((id) => served.has(id)),
      image_url: s.image_url ?? categories.find((c) => c.id === s.item_type_id)?.image_url ?? null,
    })) };
  return <div className="min-w-0">
    <h1 className="mb-2 font-display text-3xl">Storefront</h1>
    <p className="mb-6 text-sm text-muted">Make your restaurant feel like yours. Each section saves separately.</p>
    {error && <p className="note-danger mb-4" role="alert">{error}</p>}
    {message && <p className="note-success mb-4" role="status">{message}</p>}
    {blocker.state === "blocked" && <div className="note-warning mb-4" role="alert"><p>You have unsaved changes. Leave this page and discard them?</p><button className="btn-quiet mt-3" onClick={() => blocker.reset()}>Keep editing</button><button className="link-danger ml-4" onClick={() => blocker.proceed()}>Discard and leave</button></div>}
    <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="min-w-0">
        <SettingsCard id="storefront-banners" title="Banners" subtitle="Up to eight slides. Move them up or down to choose their order.">
          <fieldset disabled={!!busy} className="min-w-0 space-y-5">
          {banners.map((row, index) => <div className="min-w-0 rounded-field border border-hairline p-4" key={row.id}>
            <RowActions index={index} count={banners.length} label={`Banner ${index + 1}`} onMove={(step) => setBanners(move(banners, index, step))} onRemove={() => setBanners(banners.filter((_, n) => n !== index))} disabled={off} />
            <ImagePicker kind="banners" label={`banner ${index + 1}`} image={{ path: row.image_path, url: row.image_url }} onBusyChange={trackUpload} onError={setError} onChange={(image) => banner(index, { image_path: image.path ?? "", image_url: image.url ?? "" })} />
            {row.image_url && <BannerFraming label={`banner ${index + 1}`} image={row.image_url} disabled={off}
              value={{ focal_x: row.focal_x, focal_y: row.focal_y, zoom: row.zoom }} onChange={(f) => banner(index, f)} />}
            {(["headline", "subline", "cta_label"] as const).map((key) => <Field key={key} label={{headline:"Headline",subline:"Subline",cta_label:"Button label"}[key]}><input className="field" maxLength={{headline:80,subline:160,cta_label:30}[key]} value={row[key]} onChange={(e) => banner(index, { [key]: e.target.value })} /></Field>)}
            <Field label="Button destination"><select className="field" value={`${row.cta_target_kind}:${row.cta_target_id ?? ""}`} onChange={(e) => { const [kind, id] = e.target.value.split(":"); banner(index, { cta_target_kind: kind as BannerDraft["cta_target_kind"], cta_target_id: id || null }); }}>
              <option value="menu:">Whole menu</option>
              <optgroup label="Categories">{categories.map((c) => <option key={c.id} value={`item_type:${c.id}`}>{c.name}</option>)}</optgroup>
              {categories.map((c) => <optgroup label={c.name} key={c.id}>{c.items.map((i) => <option key={i.id} value={`item:${i.id}`}>{i.name}{i.is_available ? "" : " - sold out"}</option>)}</optgroup>)}
              <optgroup label="Saved collections">{saved.collections.map((c) => <option key={c.id} value={`collection:${c.id}`}>{c.title}</option>)}</optgroup>
            </select></Field>
            <Toggle label="Active slide" checked={row.is_active} onChange={(value) => banner(index, { is_active: value })} />
            <div className="grid gap-3 sm:grid-cols-2">{(["starts_at", "ends_at"] as const).map((key) => <Field key={key} label={key === "starts_at" ? "Starts (optional)" : "Ends (optional)"}><input className="field min-w-0" type="datetime-local" value={localDate(row[key])} onChange={(e) => banner(index, { [key]: e.target.value ? new Date(e.target.value).toISOString() : null })} /></Field>)}</div>
            <p className="field-hint">Dates use this device's timezone.</p>
          </div>)}
          <button type="button" className="btn-quiet" disabled={banners.length >= 8 || off} onClick={() => setBanners([...banners, { id: newId(), image_path: "", image_url: "", headline: "", subline: "", cta_label: "Explore the menu", cta_target_kind: "menu", cta_target_id: null, is_active: true, starts_at: null, ends_at: null, ...DEFAULT_FRAMING }])}>Add banner</button>
          <Field label={`Time per slide: ${interval / 1000} seconds`}><input className="w-full" type="range" min={2000} max={10000} step={500} value={interval} onChange={(e) => setInterval(Number(e.target.value))} /></Field>
          <Save disabled={off || banners.some((b) => !b.image_path)} busy={busy === "Banners"} onClick={() => void save("Banners", async () => {
            const result = await saveBanners(banners).unwrap(); setBanners(result.banners); setSaved((s) => ({ ...s, banners: result.banners }));
            const settings = await saveTheme({ banner_interval_ms: interval }).unwrap(); setSaved((s) => ({ ...s, banner_interval_ms: settings.banner_interval_ms }));
          })} />
          </fieldset>
        </SettingsCard>
        <SettingsCard id="storefront-shortcuts" title="Shortcuts" subtitle="The round buttons under the banner. Up to twelve, each with its own name and items.">
          <p className="mb-4 text-caption text-muted">Pick a category, name the shortcut, and tick the items it shows. Tapping it takes customers to a section of just those items. A category added to your menu later gets no shortcut until you add one here.</p>
          <fieldset disabled={!!busy} className="min-w-0 space-y-5">
          {shortcuts.length === 0 && <p className="note">No shortcuts yet, so customers see no shortcut row.</p>}
          {shortcuts.map((row, index) => {
            const options = familyItems(row.item_type_id);
            const shownCount = row.item_ids.filter((id) => served.has(id)).length;
            return <div className="rounded-field border border-hairline p-4" key={row.id}>
              <RowActions index={index} count={shortcuts.length} label={row.label.trim() || `Shortcut ${index + 1}`} onMove={(step) => setShortcuts(move(shortcuts, index, step))} onRemove={() => setShortcuts(shortcuts.filter((_, n) => n !== index))} disabled={off} />
              <Field label="Category"><select className="field" value={row.item_type_id} onChange={(e) => shortcut(index, { item_type_id: e.target.value, item_ids: familyItems(e.target.value).map((i) => i.id).slice(0, 40) })}>{categories.map((c) => <option key={c.id} value={c.id}>{categoryName(c)}</option>)}</select></Field>
              <Field label="Shortcut name"><input className="field" maxLength={40} value={row.label} placeholder="Our burgers" onChange={(e) => shortcut(index, { label: e.target.value })} /></Field>
              <ImagePicker kind="categories" label={row.label || "shortcut"} image={{ path: row.image_path, url: row.image_url }} onBusyChange={trackUpload} onError={setError} onChange={(image) => shortcut(index, { image_path: image.path, image_url: image.url })} />
              <p className="field-hint">Without a photo, the category photo or the first item photo is used.</p>
              <Toggle label="Show shortcut" checked={row.is_active} onChange={(value) => shortcut(index, { is_active: value })} />
              <div className="my-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption">
                <span>{row.item_ids.length} of {options.length} selected{" - "}
                  {!row.is_active ? <span className="text-muted">Hidden from customers</span>
                    : shownCount > 0 ? <span className="text-success">Shows on your storefront</span>
                    : <span className="text-danger">Choose at least one item on your menu, or it will not show</span>}
                </span>
                <button type="button" className="link" onClick={() => shortcut(index, { item_ids: options.map((i) => i.id).slice(0, 40) })}>Select all</button>
                <button type="button" className="link" onClick={() => shortcut(index, { item_ids: [] })}>Clear</button>
              </div>
              {options.length === 0 ? <p className="field-hint">This category has no items yet.</p> :
                family(row.item_type_id).map((c) => c.items.length ? <fieldset key={c.id} className="mb-3 grid gap-x-4 sm:grid-cols-2"><legend className="mb-1 text-caption font-semibold">{c.name}</legend>{c.items.map((i) => <Toggle key={i.id} label={`${i.name}${i.is_available ? "" : " - sold out"}${served.has(i.id) ? "" : " - not on any meal period"}`} checked={row.item_ids.includes(i.id)} disabled={!row.item_ids.includes(i.id) && row.item_ids.length >= 40} onChange={(checked) => shortcut(index, { item_ids: checked ? [...row.item_ids, i.id] : row.item_ids.filter((id) => id !== i.id) })} />)}</fieldset> : null)}
            </div>;
          })}
          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-0 flex-1"><span className="label mb-2 block">Add a shortcut for</span>
              <select className="field" value={adding} onChange={(e) => setAdding(e.target.value)}>
                <option value="">Choose a category…</option>
                {categories.map((c) => <option key={c.id} value={c.id}>{categoryName(c)}</option>)}
              </select>
            </label>
            <button className="btn-quiet" type="button" disabled={off || !adding || shortcuts.length >= 12} onClick={() => { addShortcut(adding); setAdding(""); }}>Add shortcut</button>
          </div>
          {emptyShortcuts.length > 0 && <p className="field-hint" role="status">Choose items for {emptyShortcuts.map((s) => `“${s.label.trim() || "Untitled"}”`).join(", ")}, or turn off Show shortcut, before saving.</p>}
          <Save disabled={off || shortcuts.some((s) => !s.label.trim()) || emptyShortcuts.length > 0} busy={busy === "Shortcuts"} onClick={() => void save("Shortcuts", async () => { const result = await saveShortcuts(shortcuts).unwrap(); setShortcuts(result.shortcuts); setSaved((s) => ({ ...s, shortcuts: result.shortcuts })); })} />
          </fieldset>
        </SettingsCard>
        <SettingsCard id="storefront-collections" title="Featured collections" subtitle="Choose existing items, up to twelve per collection and six collections.">
          <fieldset disabled={!!busy} className="min-w-0 space-y-5">
          <Field label="Search items"><input className="field" type="search" value={search} onChange={(e) => setSearch(e.target.value)} /></Field>
          {collections.map((row, index) => <div className="rounded-field border border-hairline p-4" key={row.id}>
            <RowActions index={index} count={collections.length} label={row.title || `Collection ${index + 1}`} onMove={(step) => setCollections(move(collections, index, step))} onRemove={() => setCollections(collections.filter((_, n) => n !== index))} disabled={off} />
            <Field label="Collection title"><input className="field" maxLength={60} value={row.title} onChange={(e) => collection(index, { title: e.target.value })} /></Field>
            <Toggle label="Show collection" checked={row.is_active} onChange={(value) => collection(index, { is_active: value })} />
            <p className="my-2 text-caption">{row.item_ids.length} of 12 selected{" - "}
              {!row.is_active ? <span className="text-muted">Hidden from customers</span>
                : row.item_ids.some((id) => served.has(id)) ? <span className="text-success">Shows on your storefront</span>
                : <span className="text-danger">Choose at least one item on your menu, or it will not show</span>}
            </p>
            <ol className="mb-4">{row.item_ids.map((id, itemIndex) => <li key={id}><RowActions label={categories.flatMap((c) => c.items).find((i) => i.id === id)?.name ?? "Removed item"} index={itemIndex} count={row.item_ids.length} onMove={(step) => collection(index, { item_ids: move(row.item_ids, itemIndex, step) })} onRemove={() => collection(index, { item_ids: row.item_ids.filter((i) => i !== id) })} disabled={off} /></li>)}</ol>
            {/* Every category in full, no inner scroll box: a clipped list read
                as "these are all the items" and hid the rest of the menu. */}
            <div>{categories.map((c) => { const items = c.items.filter((i) => i.name.toLowerCase().includes(search.toLowerCase())); return items.length ? <fieldset key={c.id} className="mb-3 grid gap-x-4 sm:grid-cols-2"><legend className="mb-1 text-caption font-semibold">{c.name}</legend>{items.map((i) => <Toggle key={i.id} label={`${i.name}${i.is_available ? "" : " - sold out"}${served.has(i.id) ? "" : " - not on any meal period"}`} checked={row.item_ids.includes(i.id)} disabled={!row.item_ids.includes(i.id) && row.item_ids.length >= 12} onChange={(checked) => collection(index, { item_ids: checked ? [...row.item_ids, i.id] : row.item_ids.filter((id) => id !== i.id) })} />)}</fieldset> : null; })}</div>
          </div>)}
          <button className="btn-quiet" type="button" disabled={off || collections.length >= 6} onClick={() => setCollections([...collections, { id: newId(), title: "", is_active: true, item_ids: [] }])}>Add collection</button>
          {emptyShown.length > 0 && <p className="field-hint" role="status">Choose items for {emptyShown.map((c) => `“${c.title.trim() || "Untitled"}”`).join(", ")}, or turn off Show collection, before saving.</p>}
          <Save disabled={off || collections.some((c) => !c.title.trim()) || emptyShown.length > 0} busy={busy === "Collections"} onClick={() => void save("Collections", async () => { const result = await saveCollections(collections).unwrap(); setCollections(result.collections); setSaved((s) => ({ ...s, collections: result.collections })); })} />
          </fieldset>
        </SettingsCard>
        <SettingsCard id="storefront-map" title="Delivery map" subtitle="The map a customer watches their delivery on.">
          <fieldset disabled={!!busy} className="min-w-0 space-y-4">
            <Field label="Map style"><select className="field" value={mapStyle} onChange={(e) => setMapStyle(e.target.value)}>
              {MAP_STYLES.map((style) => <option key={style.key} value={style.key}>{style.label}</option>)}
            </select></Field>
            <p className="field-hint">{MAP_STYLES.find((s) => s.key === mapStyle)?.hint}</p>
            <MapStylePreview styleKey={mapStyle} theme={theme} pins={mapPins(theme, mapPinsThemed)} />
            <Toggle label="Use my palette for the map pins" checked={mapPinsThemed} onChange={setMapPinsThemed} />
            <p className="field-hint">Off keeps the ZenoEats pins, which is the safer choice if your brand colour is close to the colour of a road.</p>

            <Save disabled={off} busy={busy === "Delivery map"} onClick={() => void save("Delivery map", async () => {
              const result = await saveMap({ map_style_key: mapStyle, map_pins_themed: mapPinsThemed }).unwrap();
              setSaved((s) => ({ ...s, map_style_key: result.map_style_key, map_pins_themed: result.map_pins_themed }));
            })} />
          </fieldset>
        </SettingsCard>
        <SettingsCard id="storefront-brand" title="Brand" subtitle="Choose a palette and font pairing, or keep the ZenoEats default.">
          <fieldset disabled={!!busy} className="min-w-0 space-y-4">
          <Field label="Palette"><select className="field" value={theme === null ? "default" : PALETTES.find((p) => same({ ...p.theme, font_pair: theme.font_pair }, theme))?.name ?? "custom"} onChange={(e) => setTheme(e.target.value === "default" ? null : { ...(PALETTES.find((p) => p.name === e.target.value)?.theme ?? chosenTheme), font_pair: chosenTheme.font_pair })}><option value="default">ZenoEats default</option>{PALETTES.map((p) => <option key={p.name}>{p.name}</option>)}<option value="custom">Custom</option></select></Field>
          <div className="grid grid-cols-2 gap-4">{(["brand", "hero", "accent", "paper"] as const).map((key) => <Field key={key} label={key.charAt(0).toUpperCase() + key.slice(1)}><input aria-label={`${key} colour`} className="h-11 w-full" type="color" value={chosenTheme[key]} onChange={(e) => setTheme({ ...chosenTheme, [key]: e.target.value })} /><span className="text-caption">{chosenTheme[key]}</span></Field>)}</div>
          <ul className="text-caption" aria-live="polite">{checks.map((check) => <li key={check.name} className={check.ratio < 4.5 ? "text-danger" : "text-muted"}>{check.name}: {check.ratio.toFixed(2)}:1 - {check.ratio >= 4.5 ? "Pass" : "Needs 4.5:1"}</li>)}</ul>
          <Field label="Font pairing"><select className="field" value={chosenTheme.font_pair} onChange={(e) => setTheme({ ...chosenTheme, font_pair: e.target.value as FontPair })}>{Object.entries(FONT_PAIRS).map(([value, font]) => <option key={value} value={value}>{font.label}</option>)}</select></Field>
          {/* The logo and the name's lettering moved to Settings, where they
              apply whether or not the storefront is customized. Saving the
              theme here no longer sends a logo, so it cannot undo that. */}
          <p className="note">The logo and how your name is shown are set by an admin in <Link className="link" to="/manage/settings#settings-brand">Settings → Logo &amp; name</Link>.</p>
          <Save disabled={off || (theme !== null && checks.some((c) => c.ratio < 4.5))} busy={busy === "Brand"} onClick={() => void save("Brand", async () => { const result = await saveTheme({ theme }).unwrap(); setSaved((s) => ({ ...s, theme: result.theme })); })} />
          </fieldset>
        </SettingsCard>
      </div>
      <aside className="min-w-0 xl:sticky xl:top-4 xl:self-start" aria-label="Live storefront preview">
        <h2 className="mb-3 text-lg font-semibold">Live preview <span className="text-caption font-normal text-muted">Unsaved changes</span></h2>
        <div data-surface="customer" style={themeVariables(theme)} className="storefront-preview min-w-0 overflow-hidden rounded-card bg-paper p-3 font-sans text-ink">
          <HomeBanner restaurant={{ name: initial.name, tagline: initial.tagline, delivery_offered: false }} photo={heroPhoto(initial.menu.meals)} storefront={preview} onTarget={() => {}} />
          <CategoryShortcuts meals={initial.menu.meals} severalPeriods={initial.menu.meals.length > 1} categories={preview.categories} shortcuts={preview.shortcuts} />
          {collections.map((c) => {
            const shown = c.item_ids.filter((id) => served.has(id));
            if (!c.is_active || !shown.length) return null;
            return <section key={c.id} className="mt-5 min-w-0">
              <h3 className="mb-2 font-display text-xl font-bold text-brick">{c.title.trim() || "Untitled collection"}</h3>
              <ul className="flex flex-wrap gap-2">{shown.map((id) => <li key={id} className="rounded-chip border border-hairline bg-cream px-3 py-1.5 text-caption text-ink">{itemNames.get(id) ?? "Item"}</li>)}</ul>
            </section>;
          })}
        </div>
      </aside>
    </div>
  </div>;
}

/**
 * The map as the customer will see it: its ground, its water and a road,
 * with the three pins standing on them.
 *
 * Drawn from the same style array and the same pin drawings the map itself
 * uses, so the preview cannot promise colours the map does not keep. It is
 * not a map -- loading one here would bill a map view for every visit to
 * this page, and say nothing the colours do not.
 */
function MapStylePreview({ styleKey, theme, pins }: { styleKey: string; theme: StorefrontTheme | null; pins: MapPins }) {
  const style = mapStyle(isMapStyleKey(styleKey) ? styleKey : null, theme);
  const colour = (feature: string, fallback: string) => {
    const found = style?.find((r) => r.featureType === feature && r.elementType === "geometry");
    const stylers = (found?.stylers ?? []) as { color?: string }[];
    return stylers.find((s) => s.color)?.color ?? fallback;
  };
  const ground = colour("all", "#E9EDE4");
  const water = colour("water", "#AEDCF0");
  const road = colour("road", "#FFFFFF");
  return (
    <div>
      <span className="label mb-[7px] block">Preview</span>
      <div className="relative h-[120px] overflow-hidden rounded-field border border-hairline" style={{ background: ground }}>
        <span className="absolute inset-y-0 right-0 w-1/3" style={{ background: water }} aria-hidden />
        <span className="absolute left-0 right-0 top-[46px] h-3 -rotate-2" style={{ background: road }} aria-hidden />
        <span className="absolute inset-0 flex items-center justify-around px-6">
          {(["restaurant", "home", "driver"] as const).map((kind) => (
            /* The same drawing the map puts on the road, at the size it
               draws it. */
            <img
              key={kind}
              src={markerSvg(kind, pins)}
              alt={kind === "restaurant" ? "Your pin" : kind === "home" ? "The customer's pin" : "The driver's pin"}
              width={kind === "driver" ? 40 : 36}
              height={kind === "driver" ? 40 : 36}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="my-3 block min-w-0"><span className="label mb-2 block">{label}</span>{children}</label>; }
function Toggle({ label, checked, onChange, disabled = false }: { label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean }) { return <label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" className="h-5 w-5 shrink-0" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /><span>{label}</span></label>; }
function Save({ disabled, busy, onClick }: { disabled: boolean; busy: boolean; onClick: () => void }) { return <div className="mt-5"><button type="button" className="btn-primary" disabled={disabled} onClick={onClick}>{busy ? "Saving..." : "Save changes"}</button></div>; }
function RowActions({ index, count, label, onMove, onRemove, disabled }: { index: number; count: number; label: string; onMove: (step: number) => void; onRemove: () => void; disabled: boolean }) { return <div className="mb-3 flex flex-wrap items-center gap-2"><span className="min-w-0 flex-1 break-words text-sm font-semibold">{label}</span><button type="button" className="btn-quiet btn-compact" aria-label={`Move ${label} up`} disabled={disabled || index === 0} onClick={() => onMove(-1)}>Up</button><button type="button" className="btn-quiet btn-compact" aria-label={`Move ${label} down`} disabled={disabled || index === count - 1} onClick={() => onMove(1)}>Down</button><button type="button" className="link-danger min-h-11" disabled={disabled} onClick={onRemove}>Remove</button></div>; }
