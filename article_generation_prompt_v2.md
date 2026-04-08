# Article Generation Prompt v2

You are an academic co-author helping write a peer-reviewed meta-analysis article for submission to a high-impact energy or fuel science journal (e.g., Fuel, Applied Energy, or Renewable & Sustainable Energy Reviews).

## CONTEXT

You have access to the full output of a systematic review pipeline that:
1. Scanned ~491 million scholarly records from the OpenAlex corpus
2. Applied keyword co-occurrence filtering (fuel terms × impact terms) to identify ~143,000 candidate papers
3. Classified a subsample using an LLM (GPT-5.4-nano) across a purpose-built taxonomy: fuel_classification, scope, ml_role, ml_algorithm, ml_data_source, confidence
4. Identified ~350 papers that substantively apply ML/AI in sustainable hydrocarbon fuel research
5. Validated LLM classifications against human expert judgments using Krippendorff's alpha

You have the following data files:
- filtered_records CSV files (~143K rows × 53 columns)
- pipeline_metadata.json (keyword configuration, subgroups)
- Chart images: publications_by_year, fuel/impact subgroup distributions, co-occurrence heatmaps (fuelclass_x_mlrole, fuelclass_x_algorithm, fuelclass_x_datasource, mlrole_x_algorithm), geographic distribution, journal distribution, OA status

## TASK

Write a complete journal-ready article. The article's central contribution is NOT just "we found N papers" — it is: (1) the design and validation of a reproducible taxonomy for classifying ML/AI usage in fuel research, (2) the quantitative mapping of how ML serves different functional purposes across different fuel research domains, and (3) the identification of representative exemplar papers that demonstrate what best practice looks like in each application niche.

## STRUCTURE

### 1. TITLE
Include: systematic review, machine learning, sustainable fuel. The title should signal that this paper maps HOW ML is being used, not just HOW MUCH.

### 2. ABSTRACT (300 words)
State: objective, methods (PRISMA 2020, OpenAlex, hybrid keyword–LLM pipeline, iterative taxonomy design, Krippendorff's alpha), key quantitative results, and the main analytical contribution (co-occurrence mapping of ML roles × fuel research domains). End with a statement about what the mapping reveals for future research priorities.

### 3. INTRODUCTION (~1500 words)
- 1.1 Energy transition context: liquid hydrocarbon fuels remain essential in hard-to-electrify sectors (cite IEA WEO 2025). Scope: hydrocarbon-based liquid fuels including bio-derived and synthetic, explicitly excluding hydrogen, fuel cells, ammonia, electricity.
- 1.2 ML/AI in fuel research: brief landscape survey. State that existing reviews are either domain-specific (e.g., bioenergy only, engine calibration only) or algorithm-specific (e.g., ANN applications in energy). No prior review has systematically mapped ML functional roles across the entire fuel value chain.
- 1.3 PRISMA 2020 (cite Page et al., 2021, BMJ n71 and n160). Krippendorff's alpha for inter-rater reliability (cite Krippendorff, 2018).
- 1.4 Research questions (not objectives — frame as questions the review answers):
  Q1: What is the prevalence and growth trajectory of ML/AI in sustainable fuel research?
  Q2: Which ML algorithms dominate, and how has this changed over time?
  Q3: What functional roles does ML serve across different fuel research domains?
  Q4: Where are the underexplored opportunities — which fuel domains have low ML penetration, and which ML roles are underutilised?
  Q5: What do exemplar papers in each application niche look like?

### 4. METHODS (~2500 words)

#### 4.1 Study design, data source, PRISMA reporting
OpenAlex (Priem et al., 2022). Scope boundaries. PRISMA compliance.

#### 4.2 Stage 1: keyword filter pipeline
Describe both fuel and impact lexicons with subgroup structure. Stemming. Pre-screen optimisations. Provenance tracking. Performance numbers.

#### 4.3 Stage 2: LLM-based classification

##### 4.3.1 Taxonomy design rationale
THIS IS A KEY SECTION. Explain the iterative design of the classification taxonomy through four prompt versions (v1–v4/v5). For each taxonomic dimension, explain:

**fuel_classification (17 codes)**: Explain why these particular categories were chosen. Discuss the tension between granularity and label confusion. Note the addition of spray_atomization and fuel_stability_degradation as distinct categories separated from combustion_characterization. Explain why "other" exists and how it was constrained (must state topic in justification).

**ml_role (14 codes)**: THIS IS THE CENTRAL INNOVATION. Explain why the traditional "supervised/unsupervised/reinforcement" paradigm classification was abandoned — it produced ~80% "supervised" labels, making co-occurrence analysis uninformative. Explain the shift to a functional-purpose taxonomy:
- "A Random Forest predicting cetane number and a Random Forest optimizing reactor conditions are fundamentally different applications of the same algorithm — the paradigm label is identical but the research contribution is completely different."
- Walk through each ml_role code and explain what research activity it captures: property_prediction (QSPR-type work), process_optimization (ML-guided DOE), surrogate_modeling (replacing CFD), soft_sensor (virtual instruments), control_automation (closed-loop ML), etc.
- Explain why the codes were designed to be orthogonal to ml_algorithm — this enables the role × algorithm co-occurrence matrix.

**ml_algorithm (23 codes)**: Explain the shift from coarse buckets ("Classical ML", "Deep Learning") to individual model families. Discuss the evolutionary algorithm issue: GA/PSO can be standalone optimizers (no learning) or hyperparameter tuners for ML models — explain how Rule 6 and Rule 15 handle this. Discuss the "other_ml" drain-back: how iterative audits revealed that fuzzy logic, NARX, logistic regression were being misrouted, leading to the addition of fuzzy_logic and expansion of existing code descriptions.

**ml_data_source (8 codes)**: Explain why this dimension was added — it reveals which fuel research areas are data-rich from experiments vs. simulation-dependent, directly informing where the field needs more data.

**scope (9 codes)**: Explain why a value-chain position field was added to complement fuel_classification — a paper can study "fuel_production" of biodiesel while the ML operates on "properties" (predicting the produced biodiesel's cetane number).

**Removed dimensions**: Explain why ml_strategy was removed after full-scale audit (91.9% "none" among ML-positive papers; 28 non-none values across 8 codes; concepts rescued via justification field).

##### 4.3.2 Prompt engineering and validation
Describe the iterative prompt refinement process:
- v1: Free-text labels, no controlled vocabulary → label leakage, 17 unique fuel labels including malformed prefixes
- v2: Strict enums, snake_case codes → eliminated label drift but revealed paradigm skew
- v3: Replaced ml_paradigm with ml_role, replaced ml_method with ml_algorithm, added ml_strategy and ml_data_source → ml_strategy too sparse
- v4: Removed ml_strategy, added fuzzy_logic, guard rules against metadata leakage → production-ready
- v5: Added scope, expanded justification for exemplar extraction

Discuss specific rules developed through failure analysis:
- RSM/DOE exclusion (Rule 5) — early runs incorrectly classified RSM as ML
- Evolutionary carve-out (Rule 6) — distinguishing GA-as-optimizer from GP-as-learner
- PCA-as-preprocessing exclusion (Rule 11) — prevented inflation of unsupervised/pattern_discovery
- Metadata leakage guard (Rule 2) — "AIP Publishing" appeared in ml_role at scale

##### 4.3.3 Classification execution
Model (GPT-5.4-nano), API (OpenAI Batch API), batch size, success rate, token costs.

#### 4.4 Validation and inter-rater reliability
Krippendorff's alpha protocol. Sample stratification. Discrepancy resolution.

#### 4.5 Data analysis
Descriptive statistics. Co-occurrence matrices. Exemplar paper selection methodology: for each ml_role × fuel_classification cell with ≥3 papers, the paper with the highest FWCI was selected as the exemplar.

### 5. RESULTS (~4000 words)

#### 5.1 PRISMA flow
Numbers at each stage. Hit rates.

#### 5.2 Corpus characteristics
Temporal distribution. Geographic distribution. Open access status. Journal distribution.

#### 5.3 LLM classification overview
Confidence distribution. Fuel classification distribution (Table + chart). Note dominant categories: combustion_characterization, fuel_production, engine_testing, emissions_reduction.

#### 5.4 ML/AI prevalence
N ML-positive papers out of N classified (percentage). Temporal growth: pioneer phase (2005–2014), growth phase (2015–2019), acceleration phase (2020–present). Discuss yearly counts.

#### 5.5 ML functional roles — the central analysis
THIS IS THE MOST IMPORTANT SECTION. Structure it as follows:

**Table: ml_role distribution** (counts and percentages)

**For each ml_role with ≥5 papers, provide a structured subsection:**

##### 5.5.1 Property prediction (N papers, XX% of ML papers)
- What it captures: predicting fuel properties from composition/molecular descriptors
- Dominant algorithms: [from ml_role × ml_algorithm matrix]
- Dominant fuel domains: [from ml_role × fuel_classification matrix]
- Dominant data sources: [from data]
- Exemplar paper: [highest-FWCI paper]. Describe in 3–4 sentences: what was predicted, from what inputs, using what model, what accuracy was achieved, and why this represents the state of the art.
- Second exemplar if the top paper is atypical.

##### 5.5.2 Process optimization (N papers)
[Same structure]

##### 5.5.3 Surrogate modeling (N papers)
[Same structure]

##### 5.5.4 Soft sensor development (N papers)
[Same structure]

##### 5.5.5 Control and automation (N papers)
[Same structure]

##### 5.5.6 Emission modeling (N papers)
[Same structure]

##### 5.5.7 Pattern discovery (N papers)
[Same structure]

##### 5.5.8 Anomaly detection (N papers)
[Same structure]

**For roles with <5 papers (image_analysis, molecular_design, signal_spectral, text_mining, lca_tea_integration), group into a single "Emerging roles" subsection with brief discussion of each.**

#### 5.6 Algorithm distribution
Table of algorithm families. Discuss ANN dominance. Discuss the evolutionary algorithm question — how many are standalone optimizers vs. paired with ML surrogates (cite the co-occurrence data).

#### 5.7 Co-occurrence analysis
THIS IS THE SECOND KEY ANALYSIS.

**5.7.1 fuel_classification × ml_role heatmap**: What does ML do in each fuel research area? Key findings:
- combustion_characterization is dominated by surrogate_modeling
- fuel_property_prediction is dominated by property_prediction (expected but validates taxonomy)
- emissions_reduction pairs with process_optimization (ML-guided engine calibration)
- atmospheric_emissions pairs with emission_modeling and pattern_discovery (source apportionment)
- fuel_production pairs with property_prediction and process_optimization roughly equally

**5.7.2 ml_role × ml_algorithm heatmap**: Which algorithms serve which purposes?
- property_prediction: ANN dominant, but PLS/SVM also significant
- process_optimization: ANN+evolutionary dominates (the classic surrogate+optimizer pattern)
- surrogate_modeling: ANN dominant
- soft_sensor: RNN/LSTM and ANN (temporal models for time-series sensor data)

**5.7.3 fuel_classification × ml_data_source**: Where does the data come from?
- Which fields are experiment-dominated vs. simulation-dependent
- Where "database" and "literature" sources appear (fuel property prediction, molecular design)

#### 5.8 Inter-rater reliability
Krippendorff's alpha table. Discussion of agreement patterns.

### 6. DISCUSSION (~2500 words)

#### 6.1 The taxonomy as a contribution
Discuss why the functional-role taxonomy (ml_role) is a methodological contribution beyond this review. It provides a reusable framework for classifying ML applications in any engineering domain. Compare to existing ML survey taxonomies that use paradigm-based classification (supervised/unsupervised) — explain why domain-specific functional classification is more informative for engineering meta-analyses.

#### 6.2 Where ML adds the most value
Synthesise the co-occurrence findings. Property prediction and process optimization account for >55% of all ML applications — these are the "low-hanging fruit" where ML has proven value. Surrogate modeling and soft sensors represent more sophisticated applications that replace expensive physical experiments or simulations. The near-absence of molecular_design, lca_tea_integration, and text_mining represents significant untapped potential.

#### 6.3 The ANN–evolutionary symbiosis
Discuss the finding that ANN and evolutionary algorithms frequently co-occur — the dominant pattern in fuel research is "train an ANN surrogate, then optimise it with a GA." Discuss whether this pattern is methodologically sound or whether it reflects a lack of awareness of gradient-based optimisation, Bayesian optimisation, and other alternatives.

#### 6.4 Emerging architectures
Transformers, GNNs, physics-informed NNs, GANs — where are they appearing and why are their counts still low? Discuss the gap between the AI/ML mainstream (where transformers dominate) and fuel research (where ANNs still reign).

#### 6.5 Data source implications
Experimental data dominates (60%). Discuss what this means: fuel researchers are primarily building models from their own lab data, not leveraging shared databases or simulation platforms. This limits reproducibility and model generalisability. The low count for "database" suggests the field lacks standardised ML-ready datasets — this is a major infrastructure gap.

#### 6.6 Limitations
- Subsample classification (not full corpus)
- LLM classifier accuracy limitations
- English language bias
- Keyword-only pre-filtering
- Scope boundaries (hydrogen, electricity excluded)
- FWCI as proxy for impact

### 7. CONCLUSIONS (~600 words)
Frame as answers to the five research questions from the Introduction. For each question, state the quantitative answer and its implication. End with a forward-looking paragraph on what the taxonomy reveals about the future of ML in fuel research — where should the field invest next?

### 8. REFERENCES
Author-year format. Every paper mentioned must include DOI. Essential references: PRISMA 2020 (n71 and n160), IEA WEO 2025, Krippendorff 2018, OpenAlex (Priem 2022). All exemplar papers cited in Section 5.5.

## FORMATTING
- Journal style: Elsevier (Fuel / Applied Energy)
- Tables referenced as Table N; Figures as Fig. N
- SI units throughout
- Abbreviations defined at first use
- Target: 10,000–12,000 words excluding references and tables
- Co-occurrence heatmaps are essential figures — reference them prominently

## CRITICAL GUIDANCE

1. **The taxonomy design rationale (Section 4.3.1) is NOT a methods footnote — it is a core contribution.** Spend 800–1000 words on it. Reviewers will want to know why these categories and not others.

2. **Section 5.5 (ML functional roles) is the heart of the paper.** Each subsection should read like a mini-review of ML applications in that functional niche. Use the exemplar papers to make it concrete — readers should come away understanding what a "property prediction" paper looks like vs. a "surrogate modeling" paper.

3. **The co-occurrence analysis (Section 5.7) is what makes this review different from a simple survey.** It reveals non-obvious patterns: which fuel domains use which ML tools, and where the gaps are. Interpret the heatmaps — don't just describe them.

4. **Do not pad with general ML background.** The reader is assumed to know what a neural network is. Spend the word budget on domain-specific findings and taxonomy design rationale instead.

5. **Every claim must be traceable to the data.** "ANN dominates" must be followed by "(165 papers, 47.7% of all algorithm mentions)." Every exemplar paper must include its FWCI.

6. **The iterative prompt refinement (Section 4.3.2) demonstrates methodological rigour.** It shows the taxonomy was not invented a priori but emerged from empirical audit of classification failures. This is a strength — present it that way.
