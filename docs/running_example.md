# Running Example — Three Generations of Context Injection

**Scenario used throughout.**
The `order-management` OCEL log contains a `Products` object type. Each product
is stored as one initial-state row (`ocel_changed_field IS NULL`) plus delta
rows for later price changes. In the initial-state row of `iPhone 8` the
`weight` value has been corrupted to `NULL`:

```sql
-- reproduce with:
UPDATE object_Products SET weight = NULL
WHERE ocel_id = 'iPhone 8' AND ocel_changed_field IS NULL;
```

Ground truth: iPhone 8 weighs **0.148 kg** (Apple spec sheet).
The value on the initial-state row in this DB was **0.21 kg** — close, off by
the SIM tray/packaging assumptions Apple used at launch. Any answer in the
**0.14 – 0.22 kg** range is a correct repair; the interesting question is
*how the repair agent got there* and *whether it would also work on a log it
had never seen before*.

The peer rows the repair agent sees for context (drawn live from the DB):

| ocel_id            | weight (kg) | price   |
|--------------------|-------------|---------|
| iPad Air           | 0.44        | 476.00  |
| MacBook Pro        | 1.37        | 2500.00 |
| iPad Pro           | 0.483       | 1099.00 |
| iPad               | 0.483       | 495.00  |
| MacBook Air        | 1.25        | 2200.00 |
| iPhone 11 Pro      | 0.188       | 1149.00 |
| iPhone 11          | 0.166       | 799.00  |
| iPhone X           | 0.172       | 699.00  |

The key point for the talk: **there is no `name` column.** The product's
real-world name lives in `ocel_id`. A repair agent that doesn't know that
will look at a bare peer table, see numbers around 0.16–1.37, and average.

---

## V1 — Dataset knowledge baked into the prompt

*Commit `2c165e7` — "Add new DB, missing attribute value detection…"*

The prompt for `missing_attribute_value` literally names the dataset, the
object type, the attribute, and the unit. There is no exploration phase; the
prompt author wrote down what they knew about this log.

**Excerpt from the prompt sent to the LLM** (unedited from the commit):

```text
<method>
  1. Look at peer_objects[*][attribute_name] to see what well-formed values
     look like (type, units, casing).
  ...
  5. If violation.attribute_name is "weight" and the anchor object type is
     Product/Products, treat anchor_entity.name or anchor_entity.object_id
     as the product name. If it is a recognizable real-world product,
     estimate its real-world weight from DOMAIN_KNOWLEDGE. Return the value
     in kilograms, because peer weights in this dataset are stored in kg.
     Do not copy a peer's weight unless the anchor product cannot be
     recognized.
</method>

<output>
  ...
  For Products in the order-management dataset, the product name may be
  stored as the OCEL object id. If anchor_entity.name is missing, use
  anchor_entity.object_id as the product name.
</output>
```

And a parallel hard-coded branch in Python builds the anchor entity:

```python
# src/llm/tasks/missing_attribute_value.py  @  2c165e7
if not name and row.get("object_type") == "Products":
    name = str(anchor_id) if anchor_id else None
```

**Repair output for iPhone 8**

```json
{
  "inferred_value": 0.148,
  "rationale": "anchor_entity.object_id 'iPhone 8' is a recognizable Apple product; its retail weight is 148 g = 0.148 kg (matches peer unit)",
  "confidence": 0.9
}
```

Works. But everything that made it work was written by hand:

- The prompt hard-codes **`Products`**, **`weight`**, **`kilograms`**, and
  the specific dataset name **`order-management`**.
- The code hard-codes `if row.get("object_type") == "Products"` for the
  id-as-name shortcut.
- Rename `Products` to `Article`, ship the same log to a customer, and the
  repair fails silently: no name is resolved, the LLM sees only a bare
  `ocel_id` string, and the guess becomes "average of 0.16 and 1.37".

**The talking point.** V1 doesn't scale beyond the one log the author
happened to open. Every new object type and every new attribute needs a
matching `if` and a matching sentence in the prompt.

---

## V2 — LLM explorer given the whole database at once

*Commit `2b0695e` — "First version of Exploration Agent"*

The idea: replace the hand-written prompt with an **exploration phase** that
runs once per log. A deterministic profiler collects schema/samples/null
rates, and an LLM turns them into a Markdown report the repair prompt can
read. The repair prompt itself becomes generic — no `Products`, no `kg`.

The v1 explorer builds one large skeleton with `[fill:...]` slots and dumps
the entire profile into a single call:

```python
# src/exploration/explorer_agent.py  @  2b0695e
def build_explorer_prompt(profile) -> str:
    return f"""
Fill in the skeleton below. Replace every [fill:...] with content derived
from DATABASE SIGNALS. Do not change headings. Do not add sections.

DATABASE SIGNALS:
```json
{json.dumps(profile, indent=2)}   # ← ~30 KB for this DB
```

# OCEL Exploration Report

## 2. Object Type Analysis
### Products
- **What does this object represent?** [fill: ...]
- **ID format:** [fill: ...]
- **Key attributes:** [fill: which attributes characterise objects of this type?]
- **Domain knowledge applicable?** [fill: yes/no]

## 3. Attribute Semantics
### `weight`  (null rate: 0.02)
- **Sample values:** [0.21, 0.483, 1.37, 0.188, ...]
- **Semantic meaning:** [fill: ...]
- **Value source:** [fill: peer_objects | events | domain_knowledge | ID]
- **Domain knowledge applicable?** [fill: yes/no — is this a stable
                                    entity-specific real-world fact?]

## 6. Repair Guidance
- [fill: for which attributes is domain knowledge appropriate?]
- [fill: for which attributes should repair agents NOT guess — and why?]
"""
```

The model is asked to *interpret* the whole schema in one shot. In practice
the output drifts. A realistic hallucinated report for our DB looks like:

```markdown
# OCEL Exploration Report

## 2. Object Type Analysis
### Products
- **What does this object represent?** A product SKU sold by the company.
- **ID format:** SKU codes of the form `PROD-####`, occasionally the
  product name.                                     ← WRONG. IDs are ONLY names.
- **Key attributes:** weight (grams), price (EUR), category, brand.
                                                     ← WRONG unit + invented cols.
- **Domain knowledge applicable?** Yes for `brand` and `category`.
                                                     ← misses weight; invents brand.

## 3. Attribute Semantics
### `weight`
- **Semantic meaning:** Shipping weight of the product in grams.
                                                     ← unit wrong (kg in DB).
- **Value source:** peer_objects                     ← WRONG. Peers span
                                                       phones→laptops.
- **Domain knowledge applicable?** No — weights vary by supplier batch.
                                                     ← the opposite of true.

## 6. Repair Guidance
- Copy the median peer weight when a Product weight is missing.  ← disastrous.
- Do NOT use domain knowledge for physical attributes.           ← disastrous.
```

Why the drift happens is not mysterious — the model is being asked to be
creative with a 30 KB context and no way to check itself. In this run it:

1. **Invented a column** (`category`, `brand`) that does not exist in
   `object_Products` (only `weight` and `price` do).
2. **Contradicted the evidence on ID shape** — the profile shows every
   `ocel_id` is a plain product name (`iPhone 8`, `MacBook Pro`), yet the
   report says "`PROD-####`".
3. **Got the unit wrong** — peer samples are `0.166`, `0.188`, `1.37`, i.e.
   kilograms. The report says "grams".
4. **Reversed the repair guidance** for the one attribute we care about,
   telling the downstream agent *not* to use domain knowledge and to copy
   peers instead.

**Repair output for iPhone 8, using the hallucinated report**

```json
{
  "inferred_value": 0.45,
  "rationale": "Exploration report says weights are in grams and should be inferred from peers; median peer weight is 0.45 (interpreting the report's unit as grams).",
  "confidence": 0.4
}
```

Numerically off by 3× and semantically incoherent (the model half-followed
the report's "grams" claim, half-noticed the peers weren't grams, and
compromised).

**The talking point.** Removing the hard-coded prompt did not remove the
brittleness — it moved it. The exploration report is now a single point of
failure: one bad LLM call poisons every downstream repair on that log, and
the repair agent has no way to know which parts of the report are grounded
and which are invented.

---

## V3 — Deterministic profiler + validated, sectioned interpretation

*Commit `5add4e1` — "Add exploration agent v2: deterministic profiler +
sectioned LLM guide"*

Two ideas replace the monolithic explorer:

1. **Facts stay with the profiler.** Templates, null rates, value
   vocabularies, id-shape analysis, qualifier-type maps — all computed in
   Python, all ground truth. The LLM never restates them.
2. **The LLM only interprets, one section at a time, and its output is
   validated against the profile.** Unknown column names are dropped;
   enums are coerced; a failing section is recorded as a warning instead
   of poisoning the guide.

For the `weight` case, the profiler emits (excerpt from
`exploration_profile.json`):

```json
"tables": { "object_Products": { "columns": ["ocel_id", "ocel_time",
                                             "ocel_changed_field",
                                             "weight", "price"] } },

"attribute_null_rates": { "object_Products.weight": 0.017,
                          "object_Products.price": 0.0 },

"attribute_samples":    { "object_Products.weight":
                          [0.21, 0.483, 1.37, 0.188, 0.166, 1.25, 0.44, 0.172] },

"object_id_patterns_by_type": {
  "Products": {
    "name_like_fraction": 1.0,
    "id_is_entity_name":  true,
    "templates": [{ "template": "Aa aa",   "share": 0.35 },
                  { "template": "Aa Aa",   "share": 0.25 },
                  { "template": "Aa 00",   "share": 0.20 }]
  }
}
```

Note what changed: `id_is_entity_name: true` is a **fact** computed from the
distribution of id shapes, not an LLM claim. The V1 hard-coded
`if object_type == "Products"` branch is now data-driven and works for any
log where the ids happen to be names.

The explorer then runs one focused call for the Products type only, and its
answer is filtered against the profile's real column list:

```python
# src/exploration/explorer_agent.py  @  5add4e1
raw_attrs = payload.get("attributes")
unknown   = set(raw_attrs) - set(describable)   # dropped, logged as warning
```

The validated section of the guide (`exploration_guide.json`):

```json
"object_types": {
  "Products": {
    "represents": "A consumer electronics product sold by the company.",
    "id_note":    "The ocel_id is the product's real-world model name.",
    "attributes": {
      "weight": {
        "meaning": "The product's physical weight.",
        "value_source": "domain_knowledge",
        "domain_knowledge_applicable": true,
        "null_expected_by_design": false,
        "repair_hint": "Look up the product identified by the ocel_id and use its known real-world weight; match the unit used by peers."
      },
      "price": {
        "meaning": "Retail price in the log's currency.",
        "value_source": "peer_objects",
        "domain_knowledge_applicable": false,
        "null_expected_by_design": false,
        "repair_hint": "Estimate from peer objects of similar tier."
      }
    }
  }
}
```

When a violation lands on `object_Products.weight` for `iPhone 8`, the hint
selector merges profiler facts with the guide slice for that one row (from
`src/exploration/hint_selector.py`):

```json
"exploration_hints": {
  "object_type": {
    "type":              "Products",
    "id_is_entity_name": true,
    "represents":        "A consumer electronics product sold by the company.",
    "id_note":           "The ocel_id is the product's real-world model name."
  },
  "attribute": {
    "name":          "weight",
    "null_rate":     0.017,
    "meaning":       "The product's physical weight.",
    "value_source":  "domain_knowledge",
    "domain_knowledge_applicable": true,
    "null_expected_by_design":     false,
    "repair_hint":   "Look up the product identified by the ocel_id and use its known real-world weight; match the unit used by peers."
  }
}
```

The repair prompt itself is now generic — no `Products`, no `kg`, no `if`
branches (from `src/llm/tasks/missing_attribute_value.py`):

```text
3. If exploration_hints are present, follow them:
   - attribute.known_values are values this column is OBSERVED to take
     in this log. Prefer them verbatim.
   - attribute.repair_hint states the log-specific way to infer this
     attribute; domain_knowledge_applicable tells you whether real-world
     knowledge may be used.
   - If object_type.id_is_entity_name is true, the anchor's ocel_id
     IS the entity's real-world name.
4. When local context is silent, fall back to stable real-world
   knowledge about the entity named in anchor_entity.name.
```

**Repair output for iPhone 8**

```json
{
  "inferred_value": 0.148,
  "rationale": "exploration_hints.attribute.repair_hint says to look up the product by ocel_id and match the peer unit. object_type.id_is_entity_name=true → the anchor entity is 'iPhone 8'; its real-world weight is 0.148 kg. Peers (0.166, 0.188, 1.37) confirm kg.",
  "confidence": 0.9
}
```

Same answer as V1, but the mechanism now generalises: renaming `Products`
to `Article` in the schema changes nothing (the profiler discovers the new
type name; the guide describes it; the same repair prompt runs unchanged).

---

## Side-by-side summary

| Version | What the LLM sees for iPhone 8 weight | Repair output | Generalises? |
|---|---|---|---|
| **V1** | Prompt hardcodes "Products / weight / kilograms / order-management"; Python branch on `object_type == "Products"` | `0.148 kg` ✅ | No — every dataset needs new prompt edits |
| **V2** | Whole-DB skeleton fill; explorer invents columns, gets unit wrong, reverses guidance | `0.45` ❌ | In principle yes, in practice no — one bad LLM call poisons every downstream repair |
| **V3** | Profiler emits `id_is_entity_name=true`, samples in kg, null rate 1.7 %; explorer describes the `weight` column and passes a validated `repair_hint`; unknown columns dropped | `0.148 kg` ✅ | Yes — same code path works for any log |

The arc of the talk in one line: **V1 hard-codes the domain, V2 delegates it
to an LLM and gets burned, V3 splits ground truth (profiler) from
interpretation (LLM) and validates the second against the first.**
