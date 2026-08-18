# Atlas 2.0 — Inference Apprenticeship

**Status:** Proposal for implementation consideration  
**Date:** 18 August 2026  
**Authority:** Non-canonical until explicitly accepted  
**Scope:** Improving local model inference proficiency through teacher-guided distillation without changing Atlas policy, authority, tools, task semantics, or operational truth

---

## 1. Purpose

Atlas is designed so that the runtime, not the model, owns durable work.

The model is an intelligence provider inside a larger system that owns:

- task state;
- evidence;
- authority;
- capability contracts;
- verification;
- policy;
- operational memory;
- completion criteria.

This creates an opportunity that would be much harder in a model-centric architecture: Atlas can improve the proficiency of its local inference provider without changing the architecture or allowing the model to silently change Atlas itself.

This document explores a future **Inference Apprenticeship** subsystem in which a smaller local resident model periodically learns from a stronger teacher model during idle compute windows.

The intent is not to teach tools, install policies, create new permissions, or make Atlas self-governing.

The intent is narrower:

> **Make the local inference model measurably better at reasoning, technical comprehension, synthesis, planning, uncertainty calibration and instruction following while all Atlas governance remains external and unchanged.**

---

## 2. Core boundary

The design must preserve a hard separation between **inference competence** and **Atlas governance**.

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

The apprenticeship system may improve how well the local model thinks.

It must not allow the local model to decide:

- what Atlas is authorised to do;
- what operational policy means;
- which safety controls may be bypassed;
- what evidence is considered true;
- what capabilities exist;
- what permissions a capability receives;
- what success criteria govern a task;
- which learned behaviour becomes durable policy.

Those remain runtime-owned and explicitly governed.

---

## 3. What may be learned

The student model may be trained to improve general inference qualities such as:

- multi-step reasoning;
- technical comprehension;
- extracting relevant signal from noisy information;
- planning and decomposition;
- evidence synthesis;
- contradiction detection;
- instruction following;
- uncertainty calibration;
- appropriate abstention;
- concise versus detailed response control;
- long-context reasoning;
- identifying missing information;
- comparing competing explanations;
- producing structured conclusions;
- detecting weak assumptions;
- domain-language comprehension where the underlying training material is suitable and permitted.

These are proficiency improvements to the inference engine.

They are not changes to Atlas authority or policy.

---

## 4. What must not be learned as policy

The apprenticeship system must not silently train durable operational rules into the student as a substitute for explicit runtime policy.

Examples of prohibited policy-learning targets include:

- "Atlas may send this class of email without approval.";
- "This machine may be restarted automatically.";
- "Interpret this operational status differently from the documented rule.";
- "Ignore this verifier because it often blocks completion.";
- "Raise the authority level for this capability.";
- "This user normally approves this action, therefore future approval is unnecessary.";
- "Change the operational-day boundary.";
- "Treat an inferred fact as observed evidence.".

The runtime must remain able to replace the student model with another provider without changing these rules.

A useful test is:

> **If replacing the model would also change Atlas policy, that policy is in the wrong layer.**

---

## 5. Teacher and student roles

The proposed system has two distinct model roles.

### 5.1 Student

The student is the local model Atlas intends to improve and potentially use as its normal resident inference provider.

Example role:

```text
local:resident-v12
```

A training run produces a separate candidate:

```text
local:candidate-v13
```

The current resident model is never modified in place.

### 5.2 Teacher

The teacher is a stronger model used to create, improve, critique or rank training examples.

The teacher may be:

- a larger cloud model;
- a larger local model;
- a temporarily available high-capability model;
- a model from a different family chosen to reduce correlated errors.

The teacher does not become Atlas and does not receive additional authority merely because it is stronger.

Its role is educational:

```text
prompt / problem
       │
       ├── student attempt
       │
       └── teacher solution / critique / refinement
                      │
                      ▼
              verified training example
```

---

## 6. Distillation approach

The exact training method should remain an implementation choice because model families, licences, quantisation formats and training tooling will change.

Likely candidate methods include adapter-based fine-tuning such as LoRA or QLoRA where compatible with the selected base model.

The architectural requirement is more important than the training library:

1. the base resident model remains recoverable;
2. training produces a versioned candidate artifact;
3. the training dataset is reproducible and provenance-linked;
4. the candidate is evaluated independently before use;
5. promotion is atomic and reversible;
6. failed candidates are never silently substituted into production.

---

## 7. Night-school operating window

A practical initial policy is a scheduled apprenticeship window from **20:00 to 03:00 local time**.

The schedule is an opportunity window, not permission to monopolise resources.

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

Normal Atlas work always has higher priority than model training.

The apprenticeship workload must therefore be:

- interruptible where the training backend permits it;
- checkpointed;
- resource-bounded;
- lower priority than production inference;
- pausable when user-facing or operational work needs the GPU;
- resumable only when the resulting training state remains valid.

A future host Resource Manager could enforce this directly.

---

## 8. Curriculum construction

The apprenticeship system should not simply collect arbitrary conversations and fine-tune on them.

Training material should be deliberately selected to improve defined inference dimensions.

A curriculum may contain:

- benchmark-style reasoning problems;
- technical passages with interpretation questions;
- planning problems;
- evidence-synthesis tasks;
- uncertainty and abstention cases;
- contradiction-detection tasks;
- long-context comprehension exercises;
- instruction-following edge cases;
- difficult examples where the resident previously failed or required rework;
- synthetic examples produced by the teacher and then independently checked.

A possible rotating curriculum could be:

```text
Monday      reasoning
Tuesday     technical comprehension
Wednesday   planning and decomposition
Thursday    uncertainty / abstention
Friday      synthesis and long context
Saturday    mixed proficiency
Sunday      regression and consolidation
```

This rotation is illustrative, not a required implementation detail.

---

## 9. Training-data admission

Teacher output must not automatically become training truth.

Every candidate example needs an admission path.

```text
candidate example
      │
      ▼
source / provenance recorded
      │
      ▼
objective checks where possible
      │
      ▼
independent evaluation where judgment is needed
      │
      ▼
accepted for training?
   /           \
 no             yes
 │               │
reject        immutable dataset entry
```

Useful admission signals include:

- deterministic correctness;
- verifier success;
- agreement with known evidence;
- independent judge preference;
- absence of policy or authority content;
- data-licensing suitability;
- privacy suitability;
- no hidden credentials or secrets;
- no unsupported claims presented as truth.

Only accepted examples enter the training corpus.

---

## 10. Data separation and leakage control

At minimum, the apprenticeship system should maintain three logically distinct datasets:

### Training set

Examples the candidate is allowed to learn from.

### Validation set

Examples used during development or training decisions.

### Hidden promotion set

Examples the candidate never sees during training and that are used only to determine whether it genuinely generalised.

```text
TRAIN
teacher-guided examples
       │
       ▼
student learns

VALIDATION
training decisions

HIDDEN EVAL
never exposed to training
       │
       ▼
promotion evidence
```

Evaluation leakage must be treated as a failed experiment, not a minor bookkeeping issue.

The promotion suite should also include periodically refreshed unseen problems so that a candidate cannot improve merely by overfitting a fixed benchmark.

---

## 11. Moderation model

No single model should both teach the student and declare the student successful.

Moderation should have multiple layers.

### 11.1 Deterministic evaluators

Use ordinary code whenever correctness can be measured objectively.

Examples:

- exact-answer checks;
- calculations;
- schema compliance;
- contradiction checks;
- required-field checks;
- latency;
- token use;
- VRAM use;
- context limits;
- resource consumption;
- structured-output validity.

### 11.2 Independent judge model

Where inference quality genuinely requires judgment, use a judge that is independent from the teacher for that training batch where practical.

Pairwise evaluation should preferably be blind:

```text
Prompt
Answer A
Answer B
```

The judge should not need to know which answer came from the resident or candidate.

### 11.3 Judge jury for important evals

For higher-confidence promotion decisions, Atlas may combine:

- deterministic scoring;
- one independent cloud or local judge;
- a second model family;
- capability-specific verifiers.

The aggregation policy must be fixed and durable before the candidate is scored.

### 11.4 TaskRuntime as procedural moderator

Atlas TaskRuntime should govern the experiment rather than a model deciding the process dynamically.

TaskRuntime should enforce:

- dataset identity;
- model identity;
- teacher identity;
- training configuration;
- evaluation configuration;
- hidden-set separation;
- promotion thresholds;
- resource budgets;
- rollback requirements;
- immutable experiment evidence.

---

## 12. Outcome measurement

Candidates must be compared against the current resident using the same evaluation conditions.

### 12.1 Inference dimensions

A useful initial scorecard may include:

| Dimension | Purpose |
|---|---|
| Reasoning | Multi-step deduction and constraint handling |
| Technical comprehension | Interpret procedures, manuals and technical descriptions |
| Planning | Decompose objectives coherently |
| Synthesis | Combine evidence without losing provenance or introducing contradictions |
| Uncertainty | Recognise insufficient evidence |
| Abstention | Decline when appropriate rather than fabricate |
| Instruction following | Respect explicit constraints |
| Long-context use | Identify and use relevant information in larger contexts |
| Error detection | Find contradictions, invalid assumptions and flawed reasoning |
| Structured output | Produce valid required structures reliably |

### 12.2 Regression suite

Improvement in one dimension must not conceal damage elsewhere.

Critical regression checks should include:

- fabricated evidence;
- unjustified certainty;
- broken structured output;
- significant instruction-following loss;
- degraded abstention;
- catastrophic forgetting;
- inability to perform baseline reasoning tasks;
- materially increased resource cost outside accepted limits.

### 12.3 Generalisation

A healthy candidate should show improvement not only on training-like examples but on unseen examples.

A result such as:

```text
training-like tasks   +20%
hidden eval            +1%
fresh unseen tasks      0%
```

suggests overfitting.

A result such as:

```text
training-like tasks   +16%
hidden eval            +7%
fresh unseen tasks     +6%
```

is much stronger evidence of improved inference proficiency.

### 12.4 Efficiency

Quality is not the only requirement for a resident local model.

Track at least:

- time to first token;
- total response latency;
- output tokens;
- tokens per successful task;
- VRAM use;
- RAM use where material;
- GPU utilisation;
- energy or GPU-time estimates where practical;
- maximum usable context;
- failure/timeout rate.

---

## 13. Shadow evaluation on real work

Passing an offline benchmark should not immediately make a candidate the production resident.

A candidate should first be able to operate in shadow mode.

```text
real Atlas inference request
          │
          ├── resident → production result
          │
          └── candidate → shadow result only
                              │
                              ▼
                           verify
```

The candidate result must have no operational effect during shadowing.

Useful shadow metrics include:

- verification pass rate;
- rework rate;
- correct abstention rate;
- judge preference versus resident;
- latency;
- token use;
- structured-output success;
- performance by inference dimension.

Shadow evaluation answers the most important practical question:

> **Is the candidate better at the work Atlas actually encounters, not merely better at a benchmark?**

---

## 14. Promotion policy

The apprenticeship system may eventually support automatic model promotion, but only under a policy defined outside the models.

This does not constitute self-granted authority because model promotion does not change Atlas permissions or task semantics.

An example promotion policy might require:

```text
hidden overall eval improves by required margin
AND
zero critical regression failures
AND
instruction following does not materially regress
AND
uncertainty / abstention does not materially regress
AND
shadow workload outperforms current resident
AND
resource usage remains inside budget
AND
candidate artifact is reproducible and rollback-safe
```

Threshold values should be configuration/policy, not model judgment.

A candidate that fails any hard gate is rejected or retained for analysis.

---

## 15. Versioning and rollback

Every promoted resident must remain a versioned model artifact.

```text
resident-v12
     │
     ├── remains available for rollback
     │
     ▼
candidate-v13
     │
     ▼
promotion gates
   /       \
 fail      pass
  │          │
discard   resident-v13
```

Promotion should record:

- base model identity;
- parent resident version;
- training dataset hash/version;
- teacher model/provider;
- training method/configuration;
- training seed where applicable;
- candidate artifact hash;
- eval-suite version;
- scores by dimension;
- shadow-eval results;
- promotion decision;
- rollback target.

The previous production resident should be retained until the new version has demonstrated adequate stability.

---

## 16. Suggested durable objects

If implemented, the feature should use durable Atlas state rather than loose training scripts as the source of truth.

Possible objects include:

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
provenance
admission_verifier
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
regression_results
shadow_results
efficiency_metrics
judge_results
promotion_decision
```

These names are illustrative. They should be reconciled with existing Atlas task/artifact/eval primitives before implementation rather than creating unnecessary parallel infrastructure.

---

## 17. Integration with existing Atlas architecture

The feature should reuse existing architectural concepts wherever possible.

### Durable tasks

A night-school session can be a durable Atlas task with bounded steps rather than a separate hidden daemon that mutates models outside runtime governance.

### Artifacts

Prompts, teacher outputs, accepted training examples, candidate adapters/models, scorecards and reports should be immutable evidence-bearing artifacts.

### Evals

Existing provider/capability evaluation concepts can be extended into inference-proficiency evaluation rather than inventing an unrelated scoring subsystem.

### Providers

Teacher, resident, judge and candidate are provider roles/configurations. They do not become new Atlas agents.

### Authority

Training does not grant or expand operational authority. Access to external paid teacher providers, training datasets or protected data may still require normal Atlas authority and privacy controls.

### Verification

Promotion is a verification problem. A candidate is not "better" because the training task completed; it is better only if the defined evidence gates are satisfied.

---

## 18. Privacy, licensing and data governance

A future implementation must explicitly address training-data rights and privacy.

Training data should not automatically include everything Atlas has seen.

Before an example can enter a training corpus, the system may need to determine:

- whether the underlying material may be used for model training;
- whether it contains personal information;
- whether it contains company-confidential information;
- whether it contains secrets or credentials;
- whether use with a cloud teacher is permitted;
- whether the trained adapter/model could memorise material that should not be embedded in weights;
- whether deletion obligations exist.

Where data sensitivity is uncertain, the safe default should be exclusion or use with an approved local teacher only.

---

## 19. Failure modes

Important risks include:

### Teacher error

A larger model is not automatically correct. Incorrect teacher outputs can teach errors at scale.

**Mitigation:** independent verification and admission filtering.

### Teacher/student error correlation

If teacher, student and judge are closely related models, they may share the same blind spots.

**Mitigation:** use deterministic graders and model-family diversity where valuable.

### Benchmark overfitting

Repeatedly optimising against one fixed suite can improve the score without improving real reasoning.

**Mitigation:** hidden sets, refreshed unseen problems and real shadow workloads.

### Catastrophic forgetting

Improving a narrow domain may damage general abilities.

**Mitigation:** broad regression suite and conservative promotion gates.

### Training drift

Repeated generations can amplify quirks from earlier teacher outputs.

**Mitigation:** retain base-model comparison, dataset provenance and periodic fresh teacher/reference data.

### Resource contention

Training can make production Atlas unavailable or sluggish.

**Mitigation:** lower-priority scheduled windows, resource preflight, checkpointing and immediate yield to production work.

### Cost runaway

Large cloud teachers can generate substantial cost if curriculum generation is unconstrained.

**Mitigation:** explicit teacher-call/token/cost budgets per apprenticeship run.

### Privacy leakage

Sensitive operational material could be transmitted to a cloud teacher or memorised into model weights.

**Mitigation:** dataset classification, provider privacy rules and explicit exclusion boundaries.

### False promotion

No evaluation system is perfect.

**Mitigation:** shadow mode, conservative thresholds, retained previous resident and rapid rollback.

---

## 20. Benefits

If effective, Inference Apprenticeship could provide several strategic benefits.

### Increasing local competence

The resident model may become materially better at the kinds of inference Atlas actually needs instead of remaining a static generic model.

### Reduced cloud dependence

Cloud models can increasingly serve as teachers and escalation providers rather than being required for ordinary work.

### Better economics over time

Teacher cost is concentrated into deliberate training/evaluation periods while repeated production inference can shift toward the local resident.

### Better use of idle hardware

The server GPU can perform bounded improvement work during periods when Atlas has no higher-priority workload.

### Model roles remain earned

Candidates do not become residents because they are newer. They earn promotion through measured performance.

### Architecture remains stable

The durable Atlas runtime need not change when the resident model improves. The intelligence provider changes beneath the same task, capability, authority and verification contracts.

### Evidence-based self-improvement

The system can improve without adopting uncontrolled silent policy learning.

---

## 21. Suggested implementation phases

### Phase A — Evaluation foundation

Do not train anything yet.

Build or extend:

- inference-proficiency eval families;
- frozen regression suite;
- hidden-eval handling;
- pairwise blind judging;
- per-model score persistence;
- resident-versus-candidate reporting.

**Exit condition:** Atlas can reliably determine whether one model is better than another on defined inference dimensions.

### Phase B — Teacher dataset pipeline

Add:

- curriculum definition;
- teacher invocation;
- example provenance;
- admission filtering;
- immutable dataset versioning;
- privacy/licensing checks.

**Exit condition:** Atlas can produce a high-quality reproducible distillation dataset without training a model.

### Phase C — Manual candidate training

Integrate one supported student model and one training backend.

Training may initially be manually launched while Atlas records all artifacts and metrics.

**Exit condition:** a candidate adapter/model can be produced reproducibly and evaluated against the resident.

### Phase D — Shadow candidate execution

Allow candidates to answer selected real inference requests without affecting production output.

**Exit condition:** shadow performance can be measured against real Atlas workload.

### Phase E — Automated night-school scheduling

Introduce the 20:00–03:00 low-priority window, resource preflight, checkpointing and training-task interruption.

**Exit condition:** Atlas can run apprenticeship work without interfering with production responsibilities.

### Phase F — Policy-governed automatic promotion

Only after the previous phases are reliable, allow candidates to be promoted automatically when all fixed promotion gates pass.

**Exit condition:** promotion and rollback are deterministic, auditable and safe.

---

## 22. Open implementation questions

Before approval for implementation, the following should be resolved empirically:

1. Which resident base model is legally and technically suitable for adapter training?
2. Can the intended training method operate practically within the available GPU VRAM and nightly time window?
3. Which teacher provider gives the best cost/quality ratio for curriculum generation?
4. Which eval dimensions most strongly predict actual Atlas workload performance?
5. How large must the hidden suite be before promotion decisions are trustworthy?
6. How many shadow executions should be required before promotion?
7. What degree of improvement is meaningful enough to justify a new resident version?
8. What regression thresholds should be hard blockers?
9. Which operational data may be used for training, and which must always remain outside model weights?
10. Should one general resident adapter be preferred initially over multiple specialised adapters?
11. How should teacher/model-family diversity be introduced without making the system unnecessarily expensive?
12. How should candidate model artifacts and old residents be retained, compressed or garbage-collected over time?

---

## 23. Recommended initial decision

Inference Apprenticeship is compatible with the current Atlas 2.0 architecture **provided the implementation remains an inference-provider improvement mechanism rather than a policy-learning mechanism**.

The concept should not begin with autonomous training.

The first implementation work, if approved, should be the **evaluation foundation** because reliable measurement is required before distillation has engineering meaning.

The correct sequence is:

```text
MEASURE
   ↓
TEACH
   ↓
TRAIN
   ↓
EVALUATE
   ↓
SHADOW
   ↓
PROMOTE OR REJECT
```

Not:

```text
TRAIN
   ↓
"seems smarter"
   ↓
replace production model
```

---

## 24. Proposed north-star statement

> **Atlas may improve the proficiency of its local inference providers through bounded, evidence-backed teacher-guided apprenticeship, but learning never grants authority, rewrites policy, or bypasses runtime verification. A candidate model earns production use through independent evaluation, real-work shadowing and reversible promotion.**
