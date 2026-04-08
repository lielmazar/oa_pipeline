# Prompt Revision Summary: v4→v5 (Classifier) and v1→v2 (Article)

## Classifier Prompt: v4 → v5

### New field: `scope` (9 codes)
Position in the fuel value chain the paper addresses: feedstock, production, properties, blending, combustion, emissions, aftertreatment, atmospheric, lifecycle, none.

**Rationale**: fuel_classification tells you WHAT the paper studies (e.g., fuel_production, emissions_reduction), but not WHERE in the value chain the ML operates. A paper classified as "fuel_production" might have ML predicting the cetane number of the produced biodiesel (scope=properties) or optimizing the transesterification reactor (scope=production). This distinction matters for the article's co-occurrence analysis — it enables a scope × ml_role matrix that reveals where ML is actually being applied vs. what fuel topic the paper addresses.

### Justification expanded: 40 words → 80 words (2–3 sentences)
**Rationale**: The article needs to discuss exemplar papers in each ml_role niche. The old 40-word single-sentence justification was too compressed to capture what the ML model predicts, from what inputs, with what accuracy, and any notable methodology. The expanded justification becomes a mini-abstract of the ML contribution that can be directly quoted in the article's Section 5.5 exemplar discussions.

### New Rule 17: scope definition
Clarifies that scope tracks value-chain position independent of fuel_classification.

### New Rule 18: justification content requirements
Makes the three required components explicit: (a) what ML predicts/optimizes, (b) what fuel/system, (c) notable methods or accuracy metrics.

### Updated example JSON
The output example now shows the richer justification format with accuracy metrics and multi-objective optimization mention, plus the new scope field.


## Article Generation Prompt: v1 → v2

### Structural changes

| Section | v1 | v2 | Why |
|---------|----|----|-----|
| 4.3.1 | Not present | **Taxonomy design rationale** (800–1000 words) | Core contribution was invisible in v1 |
| 4.3.2 | Brief prompt description | **Iterative prompt refinement** (v1–v5 walkthrough) | Shows taxonomy emerged empirically, not a priori |
| 5.5 | Brief role list + table | **Structured subsections per ml_role** with exemplars | Heart of the paper was hollow in v1 |
| 5.7 | Not structured | **Three co-occurrence analyses** with interpretation | The analytical payoff was missing |
| 6.1 | Not present | **Taxonomy as contribution** discussion | Positions the classification framework as reusable |
| Intro | 5 objectives | **5 research questions** | Questions are answerable; objectives are aspirational |

### Content priorities shifted

**v1 emphasis** → **v2 emphasis**

- Pipeline engineering → Taxonomy design rationale
- "We found N papers" → "Here's what ML does in each research niche"  
- Algorithm counts → Co-occurrence interpretation (which algorithms serve which purposes WHERE)
- High-FWCI name-dropping → Structured exemplar papers with specific findings per ml_role
- General ML background → Domain-specific patterns and gaps
- Limitations laundry list → Focused discussion of what the mapping reveals for future investment

### Word budget reallocation

| Section | v1 target | v2 target | Change |
|---------|-----------|-----------|--------|
| Methods | 2000 | 2500 | +500 (taxonomy rationale) |
| Results | 3000 | 4000 | +1000 (structured exemplars) |
| Discussion | 2500 | 2500 | Same budget, different focus |
| Total | 8000 | 10000–12000 | +25–50% for analytical depth |

### Key additions in article prompt

1. **Taxonomy design rationale as core contribution**: Section 4.3.1 explains WHY each ml_role code exists, WHY ml_paradigm was abandoned, WHY ml_strategy was removed. This is ~800 words that didn't exist in v1.

2. **Exemplar paper structure**: For each ml_role with ≥5 papers, v2 requires: dominant algorithms, dominant fuel domains, dominant data sources, highest-FWCI exemplar paper with 3–4 sentence description of what was done and why it matters.

3. **Co-occurrence interpretation**: Three heatmaps are not just referenced but analyzed: fuel×role ("what does ML do in each domain"), role×algorithm ("which tools serve which purposes"), fuel×datasource ("where is data coming from").

4. **Iterative prompt refinement as methodology**: v1→v5 evolution is presented as empirical taxonomy development, not a footnote. Shows label leakage, paradigm skew, strategy sparsity discoveries.

5. **Critical guidance section**: 6 explicit instructions about what matters most, preventing the LLM from padding with generic ML background or pipeline engineering details.


## Fields in v5 (final schema)
1. fuel_relevant (boolean)
2. fuel_classification (array, max 4)
3. scope (string, single value) — NEW
4. ml_role (array, max 3)
5. ml_algorithm (array, max 4)
6. ml_data_source (string, single value)
7. models (array of objects)
8. confidence (string enum)
9. justification (string, max 80 words, 2–3 sentences) — EXPANDED

## Co-occurrence matrices supported (v5)
1. fuel_classification × ml_role (headline figure — unchanged)
2. ml_role × ml_algorithm (which architectures serve which purposes — unchanged)
3. fuel_classification × ml_algorithm (algorithm popularity by field — unchanged)
4. fuel_classification × ml_data_source (experiment-driven vs simulation — unchanged)
5. scope × ml_role (NEW — where in the value chain is ML applied?)
6. scope × ml_algorithm (NEW — which algorithms at which value chain stage?)
