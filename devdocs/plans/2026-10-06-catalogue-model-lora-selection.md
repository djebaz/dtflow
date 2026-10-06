# Catalogue-driven model and LoRA selection

Status: implementation in progress in this PR.

## 1. Goal

Replace the Generate page's flat model/LoRA discovery with catalogue-driven selection while preserving the existing execution contract: the final request still carries the exact checkpoint identifier and exact LoRA identifiers/weights expected by the shared generation services.

The user-facing dependency chain is:

```text
Current provider inventory
        +
DTFlow catalogue metadata
        +
user custom LoRA metadata
        ↓
Generation domain: All / Image / Video
        ↓
Base-model family
        ↓
Exact available checkpoint / quantization
        ↓
Compatible available LoRAs
```

Prime checkpoints are the default. A compact `Include niche models` toggle adds Niche checkpoints without replacing Prime results.

This is a GUI discovery feature. The CLI remains an explicit automation/development interface and is not redesigned into an interactive domain → family → checkpoint → LoRA browser.

## 2. Existing architecture to preserve

The implementation must follow the current shared-service boundary:

- Qt does not own Draw Things gRPC or DT+ protocol behavior.
- inventory refresh remains a session/backend operation;
- request resolution, LoRA preflight and generation remain shared services rather than Qt logic;
- the selected model and LoRAs passed downstream remain exact artifact identifiers;
- cached inventory is only a prefill/observation and never proof of current availability;
- generation still refreshes live according to the existing session behavior;
- `.dtflow/last_session.json` remains the durable, credential-free desktop-session store and already scopes Generate form fields per provider under `forms`.

Do not introduce a parallel provider identity, persistence engine, request resolver, compatibility engine or inventory transport solely for this feature.

## 3. Three separate authorities

### 3.1 DTFlow bundled catalogue

Version-controlled application resources classify artifacts DTFlow knows about:

- Prime checkpoints;
- Niche checkpoints;
- known LoRAs;
- known Control/conditioning artifacts where needed by the shared catalogue schema.

The bundled catalogue describes classification and known compatibility. It does **not** establish that an artifact exists on the active provider.

Application upgrades may update bundled catalogue data.

### 3.2 User custom LoRA metadata

Custom LoRA definitions are user-owned durable data, not application whitelist entries.

The user's current custom definitions must not be committed as universal DTFlow LoRAs. DTFlow needs an extension mechanism that can persist user definitions outside packaged resources and survive application upgrades.

A custom definition may record, when known:

- exact artifact identifier;
- display name;
- base-model family;
- supported modes/domains;
- trigger prefix and other existing user-facing LoRA metadata.

Malformed user metadata must not prevent the bundled catalogue from loading.

The implementation must first inspect the current settings/runtime persistence code and use an existing suitable durable-user-data pattern where possible. Do not silently fold custom definitions into `settings.json` or another store whose ownership/schema is inappropriate merely for convenience.

### 3.3 Current provider inventory

The active provider inventory is the authority for current availability.

Catalogue membership never means installed/available. Inventory presence never means compatibility is known.

The effective selectable set is therefore a resolution/intersection of classification metadata with the active provider's inventory.

## 4. Provider-scoped persistence is required

All discovery/selection state is persisted independently for each provider using the existing provider-scoped desktop-session model.

At minimum, each provider remembers:

- generation domain: `all`, `image` or `video`;
- selected base-model family;
- selected exact checkpoint;
- `include_niche_models`;
- selected LoRAs with their existing weights/settings;
- any existing bound cloud receipt state that already belongs to that provider form.

Switching fake → local → cloud → local must restore each provider's own previous state without leaking another provider's selections.

Persist preferences/selections, **not inventory as durable truth**. On provider activation or inventory refresh:

1. load that provider's persisted form state;
2. obtain/use the provider inventory through the existing inventory/session path;
3. build the effective available catalogue;
4. restore selections that are still valid;
5. clear or explicitly present selections that are no longer available/compatible;
6. never silently substitute another checkpoint, quantization or LoRA.

If a persisted checkpoint is Niche, restoration may enable the provider's Niche toggle so the persisted state can be represented.

The existing inventory cache remains disposable and separately scoped/validated according to the runtime-layout contract. Its `cache_version` governs only the disposable inventory-cache format, and `-nc` / `--no-cache` bypasses only cache reads/writes. Neither mechanism disables, invalidates, versions, deletes or otherwise changes the bundled catalogue or durable user custom-LoRA metadata.

## 5. Catalogue concepts

Keep these concepts distinct:

- **domain**: `image` or `video`;
- **mode**: precise capabilities such as `t2i`, `i2i`, `t2v`, `i2v`;
- **base-model family**: compatibility family such as SDXL, FLUX.1, Z-Image, WAN 2.2 or LTX-2;
- **checkpoint**: exact selectable model artifact;
- **precision/quantization**: property of the exact checkpoint, not a base family;
- **tier**: Prime or Niche checkpoint classification;
- **source/provenance**: bundled or user-defined metadata;
- **availability**: resolved from the active provider inventory.

Do not infer LoRA/checkpoint compatibility solely from filenames.

## 6. Normalized catalogue layer

Add or extend a backend-neutral catalogue service/model layer rather than teaching Qt to parse JSON files.

A normalized checkpoint needs enough information to answer:

- exact artifact identity;
- base-model family;
- Prime/Niche tier;
- supported precise modes;
- derived Image/Video domain;
- known precision/quantization when supported by evidence;
- current availability after inventory resolution.

A normalized LoRA needs enough information to answer:

- exact artifact identity;
- bundled/user provenance;
- base-model family when known;
- supported modes/domains when known;
- trigger/display metadata already supported by DTFlow;
- current availability after inventory resolution.

Unknown metadata stays unknown. Do not manufacture compatibility or precision to make an item fit the new selectors.

## 7. Inventory resolution

Resolve catalogue entries against the active provider inventory using the canonical artifact identity already used by DTFlow. Prefer exact identity; do not introduce fuzzy display-name matching.

For local mode, model selection is conceptually:

```text
Prime catalogue
      ∩
current local inventory
      ↓
available Prime models
```

With Niche enabled:

```text
(Prime + Niche)
      ∩
current local inventory
```

LoRAs are:

```text
(bundled LoRAs + user custom definitions)
      ∩
current local inventory
      ↓
known available LoRAs
```

Use the same catalogue API for any provider that exposes a compatible inventory snapshot. Do not hard-code catalogue semantics specifically into the local backend when the existing backend-neutral inventory boundary can carry them.

## 8. Generate UX

The Generate model area becomes conceptually:

```text
Generation
[ All ] [ Image ] [ Video ]

Base model
[ Z-Image                              ▾ ]

Checkpoint
[ z_image_turbo_q8p.ckpt              ▾ ]
[ ] Include niche models

LoRAs
[ + Add compatible LoRA               ]
```

Follow `DESIGN.md`, the runtime token/QSS system and the current canonical Designer forms. Do not create a second visual language for these controls.

### 8.1 Generation domain

Expose:

- All (default);
- Image;
- Video.

The higher-level domain is derived from precise mode metadata:

- `t2i` / `i2i` → Image;
- `t2v` / `i2v` → Video.

The precise mode metadata remains available for compatibility decisions; the GUI does not discard it.

### 8.2 Base-model family

Populate the family selector from the **currently available eligible checkpoints**, not from a hard-coded Qt list.

A family appears only when at least one checkpoint satisfies:

- current provider availability;
- selected domain;
- Prime-only policy, or Prime+Niche when enabled.

Use stable machine identifiers internally and human-readable labels in the GUI.

### 8.3 Exact checkpoint and quantization

After a family is selected, show the exact available checkpoint artifacts for that family and domain.

Different F16/Q8P/Q6P/etc. variants remain separately selectable exact artifacts. Precision/quantization may be displayed from catalogue metadata when it is supported by evidence, but Qt must not own ad-hoc filename parsing to infer compatibility.

Do not silently substitute a different quantization when restoring state or when availability changes.

### 8.4 Prime by default; Niche is additive

`Include niche models` defaults to off.

Off:
- checkpoint discovery uses available Prime entries only.

On:
- compatible/available Niche entries are added;
- Prime entries remain;
- Prime entries sort before Niche;
- Niche may receive a subtle label;
- a valid Prime selection is preserved.

Turning the toggle off clears the selected checkpoint only if that checkpoint is no longer in the valid set.

The toggle affects checkpoint discovery only. It does not suppress or add LoRAs.

### 8.5 LoRA filtering

After selecting a checkpoint, the LoRA chooser uses the union of:

- bundled known LoRAs;
- user custom LoRA definitions;

then intersects with the current provider inventory and filters by known compatibility with the selected checkpoint/workflow.

At minimum, a known-compatible LoRA must match the selected checkpoint's base-model family. Precise mode/domain compatibility must also be applied where metadata supports it.

Compatibility remains a shared service concern. Qt renders compatible/mismatch/unknown evidence but does not invent compatibility.

Bundled versus user-defined may be shown as provenance/grouping, but it is not a compatibility category.

## 9. Unknown and unclassified artifacts

Treat these states separately:

- known compatible;
- known incompatible;
- unknown/unclassified;
- known but unavailable.

Unknown does not mean incompatible.

The curated default selectors should not pollute Prime discovery with unclassified entries or claim unsupported family/mode information. A provider inventory may therefore contain artifacts that are available but not represented in the curated family selector.

Inventory resolution must explicitly detect **new/unclassified checkpoints**: exact checkpoint identities present in the active provider inventory but absent from the bundled Prime/Niche catalogue. When one or more appear after provider activation or refresh, the Generate UI shows a non-blocking warning/alert such as `3 unclassified checkpoints found`, with an action to inspect their exact identifiers. The alert is evidence of a catalogue gap, not an error and not a compatibility claim. It should not be repeatedly redisplayed merely because the same cached observation is re-rendered; the presentation should be tied to the resolved inventory observation.

Unclassified checkpoints do not automatically enter a base-model family because DTFlow has no evidence for that classification. The implementation must inspect and preserve the current model combo's editable/raw identifier behavior as the explicit escape hatch. A user may deliberately select/type an unclassified checkpoint that is present in the active inventory. In that raw-selection state:

- keep the exact checkpoint identifier;
- represent base family as unknown/unclassified rather than inventing one;
- do not reject the checkpoint merely because the Image/Video domain filter cannot classify it;
- do not claim a precise mode, domain, family or quantization that the catalogue does not establish;
- do not offer LoRAs as known-compatible solely from filename similarity; existing preflight may still report its independent evidence;
- persist the exact checkpoint per provider so it can be restored if it remains available.

The curated domain → family → checkpoint selectors and the raw escape hatch therefore coexist: filters govern classified discovery, while explicit raw selection keeps newly appeared provider artifacts usable without pretending they are classified. The normal checkpoint and LoRA controls are selection-first searchable choosers; search is case-insensitive and matches any substring of the full artifact name. Raw identifiers live behind an explicit custom-identifier action rather than making the normal selector a free-form input.

## 10. Selection dependency and invalidation

The dependency chain is:

```text
provider inventory
    ↓
domain
    ↓
base family
    ↓
checkpoint (+ Niche policy)
    ↓
LoRAs
```

Whenever an upstream value changes, recompute downstream candidate sets.

Preserve a current selection if it remains valid. Clear it only when it no longer belongs to the valid set.

Examples:

- enabling Niche preserves a selected Prime checkpoint;
- disabling Niche clears a selected Niche checkpoint;
- changing Z-Image → FLUX.1 clears a Z-Image checkpoint and incompatible Z-Image LoRAs;
- changing provider re-resolves all selections against that provider's inventory and restores that provider's own persisted state;
- inventory refresh preserves selections still present and reports/clears those that disappeared according to the current form/presentation contract.

Do not silently keep stale incompatible LoRAs.

## 11. Ordering and search

Keep result ordering deterministic.

- use a curated display order for base families rather than machine-ID alphabetical order;
- Prime before Niche;
- stable secondary ordering for checkpoints;
- stable human-readable ordering for LoRAs;
- optional bundled/user grouping for LoRAs.

Existing searchable/editable behavior should be preserved where it is part of the current UX. Search operates within the already-filtered candidate set; it is not a replacement for catalogue compatibility filtering.

## 12. Saved configurations versus provider UI state

Do not conflate portable generation configurations with provider-scoped desktop-session preferences.

Portable saved generation configurations continue to describe the actual generation request and remain credential-free.

Provider-scoped desktop state remembers how the user was browsing/selecting on that provider, including domain/family/Niche state.

When loading a saved generation configuration:

1. keep its exact checkpoint/LoRA identifiers;
2. resolve them against the active provider inventory;
3. derive catalogue family/domain when known;
4. enable Niche if needed to represent a known Niche checkpoint;
5. restore compatible available LoRAs;
6. surface unavailable/unknown artifacts honestly;
7. never substitute another checkpoint or quantization.

## 13. Batch and other consumers

The catalogue/compatibility service is shared application logic.

Generate is the primary UX integration. Batch must consume the same catalogue service wherever it presents model/LoRA selection; do not duplicate filtering rules in Batch.

The CLI may reuse catalogue resolution/validation where that naturally fits the existing service architecture, but this PR must not redesign CLI interaction to mirror the GUI's progressive filtering.

Control/conditioning artifacts may share normalized catalogue infrastructure, but Control-specific UX is outside this change unless required by existing Generate behavior. Control artifacts must not leak into checkpoint or LoRA selectors.

## 14. Bundled catalogue and user-data lifecycle

Bundled catalogue updates are application-owned and may change with DTFlow releases.

User custom LoRA metadata:

- survives application upgrades;
- can be absent;
- is independently validated;
- is never overwritten by bundled catalogue updates;
- is never assumed available without inventory evidence.

The current user's custom Z-Image definitions are migration/input data for that user's catalogue only. They are not universal DTFlow defaults. The implemented durable boundary is `<state>/catalogue/custom_loras.json`; the bundled service tolerates that file being absent or malformed without disabling the bundled catalogue.

## 15. Data cleanup and import work

Before the bundled catalogue becomes authoritative for the GUI:

1. import the reviewed Prime, Niche, LoRA and Control classification data into `src/dtflow/resources/catalogue/`;
2. resolve known cross-classification conflicts, including the three reviewed FLUX full checkpoints removed from Control ownership;
3. ensure Prime entries have sufficient known family/domain metadata for the advertised filters;
4. preserve unknowns instead of guessing;
5. detect duplicate exact artifact identities within an artifact class;
6. expose precision/quantization only where supported by evidence;
7. keep user custom LoRAs outside packaged resources.

## 16. Implementation sequence

1. Inspect the current canonical inventory models/services, session service, generation presenter/page, LoRA panel, provider selection, `last_session` persistence, saved configuration handling, Batch model controls and relevant CLI/service communication code.
2. Record any architecture decisions needed for catalogue resource placement and user custom-LoRA persistence.
3. Add the bundled catalogue resources and schema/loader.
4. Add user custom-LoRA loading/persistence through the chosen durable user-data boundary.
5. Add normalized catalogue models and deterministic classification indexes.
6. Resolve the active provider inventory against the known catalogues by canonical exact identity.
7. Add domain/family/checkpoint queries with Prime default and additive Niche support.
8. Add compatible available LoRA queries combining bundled and user definitions.
9. Extend provider-scoped `last_session` state for domain, family and Niche preference while preserving existing per-provider checkpoint/LoRA state and schema/version behavior.
10. Wire Generate controls using the existing presenter/controller boundaries and background inventory/session flow.
11. Implement dependency recomputation with preserve-if-valid semantics.
12. Restore provider state and saved configurations without silent substitution.
13. Reuse the catalogue service from Batch where its current controls require the same selection behavior.
14. Update `README.md`, desktop/CLI documentation where behavior changes, runtime-layout documentation for new durable user data/provider state, `devdocs/plans/initial.md`, `devdocs/current_dev_status.md`, release notes and this plan with final decisions. Map the implemented catalogue/discovery scope explicitly back to the roadmap phase/scope in `initial.md` and to implemented/remaining work in `current_dev_status.md` rather than leaving this PR as an isolated side plan.

## 17. Acceptance criteria

The feature is accepted when:

- a fresh installation with no custom LoRA definitions works normally;
- provider selection/refresh produces an effective catalogue from the provider's current inventory;
- the default model browser uses only available Prime checkpoints;
- All/Image/Video narrows available base families correctly from catalogue metadata;
- choosing a family narrows to exact available checkpoint artifacts;
- quantized/precision variants remain distinct exact selections;
- `Include niche models` is off by default and adds, rather than replaces, available Niche checkpoints;
- LoRA selection combines bundled and user definitions, intersects them with active-provider availability, and presents only known-compatible choices in the curated compatible list;
- unknown inventory artifacts are not falsely classified;
- newly observed unclassified checkpoints produce a non-blocking catalogue-gap alert with inspectable exact identifiers;
- an available unclassified checkpoint remains explicitly usable through the existing editable/raw model escape hatch without being assigned an invented family/domain or receiving guessed compatible LoRAs;
- custom LoRA definitions remain user-owned and are not packaged as universal whitelist data;
- each provider independently persists domain, family, exact checkpoint, Niche preference and LoRA selection/weights;
- switching providers restores each provider's own state and revalidates it against that provider's current inventory;
- inventory refresh preserves still-valid selections and does not silently substitute missing artifacts;
- saved configurations keep exact artifact identities and remain distinct from provider-scoped UI preferences;
- Generate and Batch do not implement separate compatibility rules;
- existing generation execution still receives the exact checkpoint/LoRA identifiers expected by shared services.

## 18. Validation and safety

Use the repository's canonical validation workflow when implementation begins. Add focused coverage at the authoritative catalogue/persistence/UI layers rather than duplicating backend protocol matrices.

No real generation, training, model download, custom-LoRA upload or potentially billable DT+ action is required to validate this feature. Do not run those merely to test catalogue or GUI behavior.

Any later live generation smoke remains separately authorized and must identify the exact provider/model exercised.
