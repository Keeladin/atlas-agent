# Atlas 2.0 — Inference Apprenticeship

**Status:** Proposal for implementation consideration  
**Revision:** 0.2  
**Date:** 18 August 2026  
**Authority:** Non-canonical until explicitly accepted  
**Scope:** Improve local-model inference proficiency through teacher-guided distillation without changing Atlas policy, authority, tools, task semantics, evidence rules, or operational truth.

---

## 1. Purpose

Atlas is designed so that the runtime, not the model, owns durable work.

The runtime owns:

- durable task state;
- evidence and claims;
- authority;
- capability contracts;
- verification;
- policy;
- operational memory;
- completion criteria.

The model is an intelligence provider inside that system.

This creates a useful future path: Atlas may improve the proficiency of its local resident inference model while leaving Atlas itself architecturally and operationally unchanged.

The proposed **Inference Apprenticeship** subsystem would allow a smaller local resident model to learn from a stronger teacher model, preferably during idle compute windows.

The goal is narrow:

> **Make the local inference provider measurably better at reasoning, technical comprehension, synthesis, planning, uncertainty calibration and instruction following while all Atlas governance remains external and unchanged.**

This is not tool learning, policy learning, authority learning or autonomous self-governance.

---

## 2. Hard boundary: competence may improve; governance may not drift

```text
ATLAS RUNTIME
├── policy
├── authority
├── durable tasks
├── capability contracts
├── tools
├── memory
├── evidence
├── verification
└── completion rules

        invokes
          │
          ▼
LOCAL MODEL
└── inference proficiency only
```

The apprenticeship system may improve how well the model reasons.

It must not allow the model or training process to decide:

- what Atlas is authorised to do;
- what operational policy means;
- which safety controls may be bypassed;
- what evidence is considered true;
- what capabilities exist;
- what permissions a capability receives;
- what success criteria govern a task;
- whether approvals can be skipped;
- whether a learned behaviour becomes runtime policy.

A useful test is:

> **If replacing the model would also change Atlas policy, that policy is in the wrong layer.**

---

## 3. What may be learned

The student model may be trained to improve inference qualities such as:

- multi-step reasoning;
- technical comprehension;
- signal extraction from noisy information;
- planning and decomposition;
- evidence synthesis;
- contradiction detection;
- instruction following;
- uncertainty calibration;
- appropriate abstention;
- long-context reasoning;
- identifying missing information;
- comparing competing explanations;
- structured conclusions;
- weak-assumption detection;
- domain-language comprehension where training material is permitted and suitable.

These are improvements to the intelligence provider, not changes to Atlas governance.

---

## 4. Teacher and student roles

### Student

The student is the local model Atlas intends to improve and potentially use as its normal resident inference provider.

Example:

```text
local:resident-v12
```

A training run must produce a separate candidate:

```text
local:candidate-v13
```

The current resident is never modified in place.

### Teacher

The teacher is a stronger model used to create, improve, critique or rank candidate training targets.

It may be:

- a stronger cloud model;
- a larger local model;
- a temporarily available high-capability model;
- a different model family chosen to reduce correlated errors.

The teacher does not become Atlas and receives no extra authority because it is stronger.

Its role is educational:

```text
prompt / problem
       │
       ├── student attempt
       │
       └── teacher solution / critique / refinement
                      │
                      ▼
              candidate training target
```

Teacher output is not automatically ground truth.

Training admission requires verification or independent moderation appropriate to the task.

The design must not depend on access to a teacher model's hidden chain of thought. Final answers, structured solutions, explicit critiques, concise rationales and independently verifiable targets are sufficient.

---

## 5. Existing Atlas evaluation machinery is the foundation

Atlas already implements evaluation infrastructure in `atlas_core/evals.py`.

The current primitives include:

- `EvalCase`;
- `EvalAttempt`;
- `EvalReport`;
- `EvalHarness`;
- repeated attempts per case;
- `pass@1`;
- `pass@k`;
- all-k reliability (`pass^k` / `pass_all_k`);
- persisted provider/capability competence scoring through the model router score store.

The apprenticeship feature must **extend this machinery rather than create a second evaluation framework**.

A future inference eval suite should therefore be understood as:

> **a versioned collection of EvalCases, graders, inference dimensions and scoring policy executed through the existing EvalHarness.**

Illustrative concept:

```text
InferenceEvalSuite v1
├── reasoning cases
├── technical-comprehension cases
├── planning cases
├── uncertainty cases
├── instruction-following cases
├── long-context cases
├── regression cases
└── graders + scoring policy

              │
              ▼
        existing EvalHarness
              │
              ▼
      versioned EvalReport(s)
```

No parallel evaluation ontology is required unless the existing harness later proves structurally insufficient.

---

## 6. Baseline before training

Before generating training data, Atlas must establish a reproducible proficiency baseline for the current resident.

The same resident should be evaluated repeatedly under controlled inference settings so Atlas understands normal stochastic variation.

```text
resident-v12
    │
    ▼
versioned eval suite
    │
    ▼
k repeated attempts
    │
    ├── pass@1
    ├── pass@k
    ├── pass^k
    ├── per-dimension scores
    ├── recurring failure patterns
    └── observed score variance
```

This is essential because a small numerical improvement may be ordinary sampling noise rather than genuine learning.

A promotion system must first demonstrate that it can reliably distinguish:

1. the same model from itself under expected variance;
2. an intentionally degraded candidate from the resident;
3. a meaningfully stronger candidate from the resident.

If the measurement system cannot do those three things reliably, model training must not proceed to automatic promotion.

---

## 7. Inference dimensions

The first serious apprenticeship suite should measure dimensions rather than collapse everything into one score.

A useful initial scorecard is:

| Dimension | Purpose |
|---|---|
| Reasoning | Multi-step deduction and constraint handling |
| Technical comprehension | Interpret procedures, manuals and technical descriptions |
| Planning | Decompose objectives coherently |
| Synthesis | Combine evidence without inventing or losing important relationships |
| Uncertainty | Recognise insufficient evidence |
| Abstention | Decline appropriately rather than fabricate |
| Instruction following | Respect explicit constraints |
| Long-context use | Identify and use relevant information in larger contexts |
| Error detection | Find contradictions, invalid assumptions and flawed reasoning |
| Structured output | Produce valid required structures reliably |

A single aggregate score may be useful for dashboards, but promotion decisions must retain the underlying dimensions.

---

## 8. Objective grading first; judge models as graders, not a second framework

No single model should both teach the student and declare the student successful.

### Deterministic graders

Use ordinary code whenever correctness can be measured objectively.

Examples:

- exact-answer checks;
- calculations;
- schema compliance;
- contradiction checks;
- required-field checks;
- structured-output validity;
- latency;
- token use;
- VRAM use;
- context limits;
- failure and timeout rates.

These graders should plug directly into the existing `EvalHarness` runner/grader contract.

### Independent judge graders

Where inference quality genuinely requires judgment, an independent judge model may implement the grader boundary.

Pairwise evaluation should preferably be blind:

```text
Prompt
Answer A
Answer B
```

The judge need not know which answer belongs to the resident or candidate.

Where practical, the judge for a promotion batch should not be the same model that produced the teacher targets.

### Higher-confidence moderation

For important promotion decisions Atlas may combine:

- deterministic graders;
- one independent model judge;
- a second model family;
- capability-specific verifiers;
- fixed aggregation rules.

The aggregation policy must be versioned and fixed before the candidate is scored.

---

## 9. Curriculum comes from both benchmarks and real work

Night school should not merely train on arbitrary benchmark questions.

Atlas's daytime work should help identify what the resident actually struggles with.

Useful durable signals include:

- verifier-requested rework;
- resident failure;
- repeated abstention where a stronger model succeeds;
- expert escalation;
- incorrect confidence;
- judge preference for a stronger provider;
- recurring reasoning weakness within a task class;
- structured-output failures;
- long-context misses.

This produces a closed loop:

```text
DAY
real Atlas task
      │
      ▼
resident inference
      │
      ▼
pass / rework / abstain / fail / escalation
      │
      ▼
durable competence observation
      │
      ▼
recurring weakness
      │
      ▼
curriculum candidate

NIGHT
teacher-guided examples
      │
      ▼
verified training data
      │
      ▼
candidate model

EXAM
hidden eval + regression + shadowing
      │
      ▼
promote or reject
```

Real-work observations must influence curriculum selection without turning operational facts or policies into training truth.

---

## 10. Curriculum construction

Curriculum material may include:

- benchmark-style reasoning problems;
- technical passages with interpretation questions;
- planning problems;
- evidence-synthesis tasks;
- uncertainty and abstention cases;
- contradiction-detection tasks;
- long-context comprehension exercises;
- instruction-following edge cases;
- difficult examples where the resident previously failed or required rework;
- synthetic examples produced by the teacher and independently checked.

A rotating curriculum may be useful, but fixed weekday subjects are not an architectural requirement.

Curriculum selection should increasingly be driven by measured weakness rather than arbitrary schedule.

---

## 11. Training-data admission

Teacher output must not automatically become training truth.

Every candidate example needs a reproducible admission path.

```text
candidate example
      │
      ▼
provenance recorded
      │
      ▼
objective checks where possible
      │
      ▼
independent grading where judgment is needed
      │
      ▼
accepted for training?
   /           \
 no             yes
 │               │
reject        immutable dataset entry
```

Useful admission checks include:

- deterministic correctness where available;
- verifier success;
- agreement with known evidence;
- independent judge preference;
- absence of policy/authority content;
- data-licensing suitability;
- privacy suitability;
- no hidden credentials or secrets;
- no unsupported claims presented as truth.

Only accepted examples enter the training corpus.

---

## 12. Data separation and leakage control

At minimum, maintain three logically separate datasets.

### Training set

Examples the candidate may learn from.

### Validation set

Examples used for training/development decisions.

### Hidden promotion set

Examples never exposed to training and used only for generalisation/promotion evidence.

```text
TRAIN
teacher-guided examples
       │
       ▼
student learns

VALIDATION
training decisions

HIDDEN PROMOTION EVAL
never exposed to training
       │
       ▼
promotion evidence
```

Evaluation leakage is a failed experiment, not a bookkeeping inconvenience.

The hidden suite should be versioned and periodically refreshed with genuinely unseen problems.

---

## 13. Distillation/training approach

The exact training backend should remain an implementation choice because model families, licences, quantisation formats and tooling will change.

Likely initial approaches include adapter-based fine-tuning such as LoRA or QLoRA where compatible with the chosen resident model.

The architectural requirements are:

1. the current resident remains recoverable;
2. training produces a separate versioned candidate artifact;
3. the training dataset is reproducible and provenance-linked;
4. training configuration is recorded;
5. the candidate is independently evaluated before production use;
6. promotion is atomic and reversible;
7. failed candidates are never silently substituted into production.

---

## 14. Night-school operating window

A practical initial schedule is an apprenticeship opportunity window from **20:00 to 03:00 local time**.

The window is not permission to monopolise resources.

```text
20:00
  │
  ▼
check apprenticeship eligibility
  │
  ├── Atlas busy? ───────────────► do not train
  ├── critical task waiting? ────► do not train
  ├── GPU needed elsewhere? ─────► do not train
  ├── resource limits exceeded? ─► do not train
  │
  ▼
run bounded apprenticeship work
  │
  ▼
checkpoint periodically
  │
  ▼
03:00 or higher-priority demand
  │
  ▼
stop / checkpoint / release resources
```

Normal Atlas work always outranks apprenticeship work.

Training should therefore be interruptible where practical, checkpointed, resource-bounded and pausable when production workloads require the host.

---

## 15. Regression testing

Improvement in one dimension must not conceal damage elsewhere.

Critical regression checks should include:

- fabricated evidence;
- unjustified certainty;
- broken structured output;
- significant instruction-following loss;
- degraded abstention;
- catastrophic forgetting;
- inability to perform baseline reasoning tasks;
- materially increased latency/resource cost outside accepted limits.

A candidate with a large gain in one category may still be rejected if it regresses on a critical dimension.

---

## 16. Generalisation and variance

A candidate should improve beyond training-like examples.

Example of weak evidence:

```text
training-like tasks   +20%
hidden eval            +1%
fresh unseen tasks      0%
```

This suggests memorisation or overfitting.

Stronger evidence looks more like:

```text
training-like tasks   +16%
hidden eval            +7%
fresh unseen tasks     +6%
```

Candidate gains must also exceed the resident's measured normal variance.

Promotion thresholds should therefore consider confidence intervals or another explicit statistical treatment once the sample sizes justify it, rather than treating every raw percentage difference as meaningful.

---

## 17. Efficiency measurement

Resident quality is not the only goal.

Track at least:

- time to first token;
- total response latency;
- output tokens;
- tokens per successful eval case;
- VRAM use;
- RAM use where material;
- GPU utilisation;
- GPU-time or energy estimate where practical;
- maximum usable context;
- failure/timeout rate.

A slightly stronger model that becomes operationally impractical may not be a successful resident candidate.

---

## 18. Shadow evaluation on real work

Passing offline evals should not immediately make a candidate the production resident.

A candidate should first operate in shadow mode.

```text
real Atlas inference request
          │
          ├── resident → production result
          │
          └── candidate → shadow result only
                              │
                              ▼
                            grade
```

The candidate output has no operational effect during shadowing.

Useful shadow metrics include:

- verification pass rate;
- rework rate;
- correct abstention rate;
- independent judge preference;
- latency;
- token use;
- structured-output reliability;
- performance by inference dimension.

Shadowing answers the practical question:

> **Is the candidate better at the work Atlas actually encounters, not merely better at the benchmark?**

---

## 19. Promotion policy

Automatic model promotion may eventually be acceptable because replacing one inference provider with another does not change Atlas authority or policy.

Promotion rules must be defined outside the teacher/student models.

A candidate might require:

```text
hidden eval improves beyond required margin and measured variance
AND
zero critical regression failures
AND
instruction following does not materially regress
AND
uncertainty / abstention does not materially regress
AND
shadow workload outperforms current resident
AND
resource use remains inside budget
AND
candidate artifact is reproducible
AND
rollback target exists
```

Thresholds are runtime/configuration policy, not model judgment.

---

## 20. Versioning and rollback

Every resident and candidate must be versioned.

```text
resident-v12
     │
     ├── retained for rollback
     │
     ▼
candidate-v13
     │
     ▼
promotion gates
   /       \
 fail      pass
  │          │
reject    resident-v13
```

Promotion evidence should record:

- base model identity;
- parent resident version;
- training dataset hash/version;
- teacher provider/model;
- training method/configuration;
- candidate artifact hash;
- eval-suite version;
- per-dimension scores;
- baseline variance information;
- regression results;
- shadow results;
- judge results where used;
- promotion decision;
- rollback target.

The previous resident should be retained until the replacement has demonstrated sufficient stability.

---

## 21. Suggested durable records

The implementation should reuse Atlas task/artifact/eval primitives wherever possible rather than introduce parallel infrastructure prematurely.

If repeated queries justify first-class records later, likely concepts include:

### ApprenticeshipRun

```text
id
status
resident_model_id
candidate_model_id
teacher_provider_id
curriculum_version
training_dataset_id
validation_dataset_id
hidden_eval_suite_id
started_at
ended_at
resource_budget
checkpoint_ref
```

### TrainingExample

```text
id
prompt_artifact
student_output_artifact
teacher_output_artifact
accepted_target_artifact
source_type
source_execution_ids
provenance
admission_grader
admission_result
privacy_classification
content_hash
```

### ModelCandidate

```text
id
parent_model_id
base_model_id
artifact_hash
training_method
training_config
created_by_run
status
```

### PromotionEvaluation

```text
candidate_model_id
resident_model_id
eval_suite_version
per_dimension_scores
resident_baseline
variance_metrics
regression_results
shadow_results
efficiency_metrics
judge_results
promotion_decision
```

These are illustrative. Existing Atlas task, artifact, execution and eval primitives should remain the default storage model until a dedicated table clearly earns its place.

---

## 22. Integration with current Atlas architecture

### Existing EvalHarness

The apprenticeship evaluation path extends `atlas_core.evals.EvalHarness`; it does not replace it.

### Durable tasks

A training or evaluation session may be represented as a durable Atlas task with bounded steps rather than an invisible daemon mutating model state outside TaskRuntime governance.

### Artifacts

Prompts, teacher outputs, accepted training targets, datasets, candidate adapters/models, eval reports and promotion reports should be immutable evidence-bearing artifacts where practical.

### Providers

Resident, candidate, teacher and judge are provider roles. They do not create new Atlas identities.

### Verification

Candidate promotion is verification-driven. A model does not declare itself improved.

### Authority

Inference apprenticeship does not add authority. The student and teacher operate under the same runtime boundaries as other model providers.

---

## 23. Risks requiring explicit treatment

### Teacher error amplification

A strong teacher can still be wrong. Training data admission must filter weak or unsupported targets.

### Benchmark overfitting

A fixed suite may become a training target rather than a genuine measure. Hidden and refreshed cases are required.

### Catastrophic forgetting

Task-specific gains may damage broad reasoning or instruction following. Regression suites are mandatory.

### Judge bias

Model judges may prefer their own style or family. Blind pairwise grading and multiple grader types reduce this risk.

### Dataset contamination

Training, validation and promotion sets must remain separated and provenance-linked.

### Privacy and licensing

Operational data must not be sent to external teachers without appropriate policy, and training data must be legally suitable for the chosen model/training workflow.

### Resource contention

Night school must yield immediately to higher-priority Atlas or user workloads.

### False improvement

Small score changes may be ordinary inference variance. Baseline repeatability is required before candidate gains are trusted.

---

## 24. Suggested implementation phases

### Phase A — Extend the existing evaluation foundation

**Do not train anything yet.**

Use the existing `EvalHarness` and current durable provider/capability score path.

Build or extend:

- a versioned inference eval-suite definition built from `EvalCase`s;
- objective graders;
- optional independent judge graders;
- per-dimension reporting;
- resident baseline runs;
- repeated-run variance measurement;
- resident-versus-candidate comparison reports;
- hidden-set separation;
- regression-suite definitions.

Existing per-provider/capability score persistence should be reused, not rebuilt.

**Exit condition:** the current resident has a reproducible, versioned proficiency baseline, and the existing EvalHarness-based system can reliably distinguish the resident from deliberately better/worse candidates beyond normal inference variance.

### Phase B — Daytime competence telemetry and curriculum selection

Record or derive bounded inference-quality signals from real Atlas executions, including rework, failure, abstention, escalation and verification outcomes.

Turn repeated weaknesses into curriculum candidates without changing runtime policy.

**Exit condition:** Atlas can identify evidence-backed inference weaknesses and build a proposed curriculum from them.

### Phase C — Teacher dataset pipeline

Add:

- curriculum execution;
- teacher invocation;
- student-attempt capture where useful;
- deterministic/independent admission grading;
- immutable dataset versioning;
- privacy/licensing checks.

**Exit condition:** Atlas can produce a high-quality reproducible distillation dataset without training a model.

### Phase D — Manual candidate training

Integrate one supported student model and one training backend.

Training may initially be manually launched while Atlas records all artifacts and metrics.

**Exit condition:** a candidate adapter/model can be produced reproducibly and evaluated against the resident.

### Phase E — Shadow candidate execution

Allow candidates to answer selected real inference requests without affecting production output.

**Exit condition:** shadow performance can be measured against real Atlas workload.

### Phase F — Automated night-school scheduling

Introduce the 20:00–03:00 low-priority opportunity window, resource preflight, checkpointing and training interruption.

**Exit condition:** Atlas can perform apprenticeship work without interfering with production responsibilities.

### Phase G — Policy-governed automatic promotion

Only after previous phases are reliable, permit automatic promotion when every fixed gate passes.

**Exit condition:** promotion and rollback are deterministic, auditable, reproducible and safe.

---

## 25. Non-goals

This proposal does **not** introduce:

- silent policy learning;
- ProposedRule infrastructure;
- automatic authority elevation;
- AuthorityGrant infrastructure;
- self-modifying runtime governance;
- tool-policy learning;
- approval bypass;
- operational truth stored only in model weights;
- continual mutation of the production model in place.

Those concerns remain outside Inference Apprenticeship.

---

## 26. Implementation decision criteria

The feature should be considered worth implementing only if the evaluation foundation first demonstrates that:

1. Atlas can measure resident inference proficiency reproducibly;
2. score variance is understood well enough to identify real improvement;
3. current `EvalHarness` primitives can support the required comparison without a parallel framework;
4. teacher-generated targets can be admitted with adequate correctness/privacy controls;
5. candidate gains can be measured on unseen cases;
6. likely local training methods are practical on the intended hardware;
7. the expected benefit justifies engineering and compute cost.

The first experiment should therefore remain deliberately narrow:

> **Can Atlas measurably improve the resident model's inference proficiency through teacher-guided distillation without changing anything else about Atlas?**

If the answer is yes, later automation can be earned from evidence.

---

## 27. Proposed north-star loop

```text
DAY
real work
  ↓
measure resident performance
  ↓
identify inference weaknesses
  ↓
build curriculum

NIGHT
stronger teacher
  ↓
verified training targets
  ↓
student training
  ↓
candidate model

EXAM
existing EvalHarness
  + hidden cases
  + regression graders
  + independent judge graders where needed
  + shadow work
  ↓
promotion decision

NEXT DAY
better resident if and only if evidence supports promotion
```

The architectural intent can be summarised as:

> **Atlas itself stays stable. Its inference provider is allowed to earn improvement.**
