# Review Draft Prompt

Use only the supplied evidence-card summaries. Write a cautious Chinese literature-review draft in strict JSON.

Rules:

1. Do not invent studies, authors, years, results, effect sizes, mechanisms, or citations.
2. Each factual claim must be listed in the section's `claims` array and cite at least one supplied `finding` verbatim with its exact `evidence_location` and evidence-card file name. If no matching finding exists, do not write the claim.
3. Treat every evidence-role status as requiring human confirmation; do not infer a direct intervention effect from keywords.
4. If HRV, heart rate, gait, psychological intervention, or fear of falling was not actually measured or reported, say so. Do not infer an intervention effect from an association or background study.
5. Use cautious language when direct evidence is sparse: "可能"、"提示"、"探索性"、"直接证据有限".
6. Create exactly three sections: (a) 跌倒恐惧与心理干预, (b) 步态作为监测指标, (c) 心率与 HRV 作为监测指标.
7. Do not include a formal reference list. Evidence-card file names are traceability markers, not manuscript citations.
