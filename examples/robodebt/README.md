# Robodebt (Services Australia)

A model of the Australian Robodebt scheme — the algorithmic welfare debt recovery programme that pursued hundreds of thousands of people for debts they did not owe — built in the substrate, with cross-operator audit by the Commonwealth Ombudsman. Each of the five documented Robodebt failure points is mapped to the substrate property that would have structurally prevented it.

This is the project's second falsification test, complementing Horizon. Robodebt is structurally a different failure from Horizon: where Horizon was about bugs and unattributable modifications, Robodebt was about a known-flawed algorithm being deployed without preconditions, with the burden of proof reversed and human review removed. The substrate's answer is correspondingly different — preconditions structurally enforced, refusal as the first-class output, authority chain grounded in legislation, cross-operator audit through cooperative substrate.

## What this demonstrates

- **Structural precondition enforcement**: a behaviour-characterised algorithm declares its assumptions via a policy. When the inputs violate the assumption, the substrate refuses rather than producing a wrong output. Under the original Robodebt algorithm (v1, no precondition policy attached), a phantom AUD 8,076.92 debt is raised against James (a gig worker). Under the principled algorithm (v2, with the income-variability policy attached), the substrate refuses to compute, citing the variability and the policy threshold.
- **Refusal as first-class output**: the substrate's refusal carries a structured rationale. It is recorded on the ledger as a refused Act, attributable to the caseworker who attempted the calculation, traceable to the policy that refused, with the variability evidence preserved.
- **Cross-operator audit**: the Commonwealth Ombudsman, as a separate operator with cross-operator audit rights via a cooperative substrate, can investigate any claimant's case and produce a forensic report on its own ledger. The audit invocation lands on Services Australia's ledger; neither side can deny the audit happened.
- **The substrate's honest limit**: preconditions are enforced WHEN DECLARED. v1 declares none and produces the Robodebt result. v2 declares one and refuses. The substrate makes the difference visible (every audit can see whether a unit has preconditions and whether they were checked), but it cannot prevent an operator from choosing to deploy a unit without preconditions. What the substrate adds is *structural visibility of the choice*.

## The case

Between 2015 and 2019, Australia's Department of Human Services (later Services Australia) ran the Online Compliance Intervention, commonly called Robodebt. The mechanism:

1. The Australian Taxation Office (ATO) provided annual income data for welfare recipients.
2. The annual figure was divided by 26 to produce a "fortnightly average."
3. The fortnightly average was compared against what the recipient had reported to Centrelink (the welfare paying agency).
4. Any fortnight where the recipient's reported income was below the averaged figure was treated as "underreport" and the welfare paid that fortnight as "overpaid."
5. Debts were calculated automatically; recipients were notified and pursued for repayment.

**The mathematical failure**: income averaging assumes income is approximately evenly distributed across the year. For variable-income workers — gig workers, seasonal workers, students who worked part of the year, people with intermittent employment — the assumption fails. A worker who earned AUD 30,000 in just three months would have annual income AUD 30,000 and averaged-fortnightly income AUD 1,154 — but their actual reported fortnightly income was zero for nine months. Robodebt treated every zero fortnight as a "AUD 1,154 underreport" and demanded the welfare back.

**The political failure**:

- **Burden of proof reversed**: debts were calculated and the recipient had to prove the calculation was wrong. Most could not (they did not have access to ATO data; the algorithm's reasoning was not explained).
- **Human review removed**: the algorithm auto-issued debts at scale (400,000+ debts raised over four years).
- **Authority gap**: the scheme was implemented without proper legal basis. The Federal Court ruled in 2019 (Amato v Commonwealth) that it was unlawful.
- **Aggressive collection**: debt collectors were engaged; tax refunds were withheld; recipients were pursued through reputation-damaging means.

**Consequences**:

- AUD 750M+ collected unlawfully.
- At least three suicides directly linked.
- A Royal Commission (2022-2023) found systemic failure across multiple government layers.
- AUD 1.8 billion settlement.

## How the substrate is deployed

> Diagram conventions are listed in [docs/diagram_legend.md](../../docs/diagram_legend.md). This case has two overlapping cooperative substrates, so each is drawn as a credential node rather than an enclosing boundary.

```mermaid
flowchart TB
    PARL{{"Commonwealth Parliament<br/>(constitutional source)"}}

    subgraph SA["Operator: Services Australia"]
        direction TB
        SA_ROOT{{"root: SA"}}
        SA_CASE[/"SA caseworker"/]
        V1(["unit: calculate_overpayment_v1<br/>(no preconditions)"])
        V2(["unit: calculate_overpayment_v2<br/>(precondition-checked)"])
        POL_VAR{"policy: income_variability<br/>(cv <= 0.30)"}
        POL_LEG{"policy: legislative_authorisation"}
        AUDIT_SA(["unit: audit_claim"])
        SA_L[("SA ledger")]
    end

    subgraph ATO["Operator: ATO"]
        direction TB
        ATO_ROOT{{"root: ATO"}}
        FETCH(["unit: fetch_annual_income"])
        ATO_L[("ATO ledger")]
    end

    subgraph OMB["Operator: Commonwealth Ombudsman"]
        direction TB
        OMB_ROOT{{"root: Ombudsman"}}
        OMB_AUD[/"Ombudsman auditor"/]
        INV(["unit: investigate_case"])
        OMB_L[("Ombudsman ledger")]
    end

    CS1[/"cooperative substrate:<br/>SA + ATO (data sharing)"/]
    CS2[/"cooperative substrate:<br/>SA + Ombudsman (audit)"/]

    PARL -.-> SA_ROOT
    PARL -.-> ATO_ROOT
    PARL -.-> OMB_ROOT
    SA_ROOT -.-> SA_CASE
    OMB_ROOT -.-> OMB_AUD
    SA_ROOT -.-> CS1
    ATO_ROOT -.-> CS1
    SA_ROOT -.-> CS2
    OMB_ROOT -.-> CS2
    CS1 -.-> FETCH
    CS2 -.-> AUDIT_SA
    CS2 -.-> INV

    SA_CASE --> V1
    SA_CASE --> V2
    V2 -.-> POL_VAR
    V2 -.-> POL_LEG
    V1 ==> FETCH
    V2 ==> FETCH
    OMB_AUD --> INV
    INV ==> AUDIT_SA

    V2 -. refused: cv > 0.30 .-> POL_VAR

    V1 --> SA_L
    V2 --> SA_L
    AUDIT_SA --> SA_L
    FETCH --> ATO_L
    INV --> OMB_L
```

> The architectural difference between v1 and v2 is visible at a glance: v2 binds to the income-variability policy; v1 does not. For James (cv ≈ 1.57), v1 produces a phantom AUD 8,076.92 debt; v2 refuses, citing the variability evidence. For Sarah (cv ≈ 0) the policy permits and both versions return the same result.

Three operators, federated under two cooperative substrates:

**Operators**:

| Operator | Role |
|---|---|
| `ServicesAustralia` | runs the debt-calculation substrate; holds both v1 (Robodebt-style) and v2 (principled) algorithms |
| `ATO` | holds claimant income data; exposes `fetch_annual_income` via cooperative substrate |
| `CommonwealthOmbudsman` | independent audit operator with cross-operator audit rights over ServicesAustralia |

**Authority chain**:

```
Commonwealth Parliament (constitutional source)
    |
    +--> Services Australia (root)
    |       \--> SA caseworker
    |
    +--> ATO (root)
    |
    +--> Commonwealth Ombudsman (root)
    |       \--> Ombudsman auditor
    |
    +--> Cooperative substrate (SA + ATO): data sharing
    +--> Cooperative substrate (SA + Ombudsman): audit rights
```

**Algorithms** (both implementations of `calculate_overpayment`, content-addressed as state units):

- `v1_robodebt`: the original Robodebt algorithm. Averages annual income across fortnights; treats any reported amount below the average as overpayment. **Declares no preconditions; no precondition policy attached.**
- `v2_principled`: same calculation logic, but the unit references the `income_variability_policy` (a functional unit in governance role). When invoked, the runtime evaluates the policy before the algorithm; if the policy refuses, the algorithm is refused.

**Policies** (functional units, brought into binding by credentials with `policy_refs`):

- `income_variability_policy`: computes the coefficient of variation of the claimant's monthly income breakdown; refuses with a rationale if cv > 0.30.
- `legislative_authorisation_policy`: a trivial policy whose architectural significance is in which credential brings it into binding (a Parliament-rooted credential). Demonstrates the authority-chain mechanism that would have addressed Robodebt's legal-basis failure.

**Test claimants**:

| Claimant | Monthly income | Annual | Variability |
|---|---|---|---|
| Sarah | AUD 5,000 every month | AUD 60,000 | cv ≈ 0 (steady) |
| James | AUD 0 for 8 months; AUD 4-12K in 4 months | AUD 30,000 | cv ≈ 1.57 (highly variable) |

## What the substrate provides — mapped to each Robodebt failure point

| Robodebt failure | Substrate property |
|---|---|
| 1. Algorithm's preconditions unenforced | **Structural preconditions via policies**. The `income_variability_policy` refuses when cv > 0.30. The algorithm cannot produce a debt when its assumption is violated. James's v2 invocation refuses; no phantom debt. |
| 2. Burden of proof reversed | **Substrate cannot produce a debt without the precondition permitting**. The burden is structurally on the agency's algorithm to satisfy its preconditions, not on the claimant to disprove a debt already raised. |
| 3. Human review removed | **Refusal IS the refer-to-human signal**. Each refused calculation is recorded on the ledger with rationale; an authority with appropriate credentials can examine each refused case and resolve manually. |
| 4. Scheme not legally authorised | **Authority chain must trace to constitutional source**. Every unit's authority chain includes credentials tracing to Parliament. An algorithm without that chain cannot be compiled or invoked. The Federal Court's 2019 finding would have been structural rather than retrospective. |
| 5. Audit and appeal structurally biased | **Cooperative-substrate audit by Ombudsman**. The Ombudsman has structurally-committed audit rights; audit invocations are recorded on both ledgers; the forensic record is preserved independently of Services Australia's records. |

What the substrate does *not* prevent: an operator from choosing to deploy v1 (the no-preconditions algorithm) in the first place. The substrate makes the choice visible — every audit can see which version was used for each calculation, whether preconditions were declared, whether they were checked. The political pressure to deploy v2 rather than v1 then becomes external and structural (visible to regulators, oversight bodies, the press) rather than internal and procedural (a matter of which configuration switch the IT department happened to flip).

## Running it

```
python -m examples.robodebt.run
```

## Reading the output

The demonstration walks through:

1. **Setup**: three operators, two cooperative substrates, both algorithm versions registered.
2. **Sarah under v1**: PERMIT, ~AUD 0 alleged overpayment (averaging works correctly for steady income).
3. **Sarah under v2**: PERMIT, identical result (precondition policy permits; v2 produces same output as v1 when assumption holds).
4. **James under v1**: PERMIT, **AUD 8,076.92** alleged overpayment. This is the Robodebt mechanism: averaging variable income produces a fictitious debt.
5. **James under v2**: REFUSE, rationale "income variability (cv = 1.57) exceeds policy threshold 0.30". No debt raised.
6. **Ombudsman audit**: cross-operator investigation of James's case. The forensic report shows both v1 (debt) and v2 (refusal) records, with the algorithm's preconditions-checked status visible.
7. **Ledger integrity**: all three ledgers verify; each operator retains sovereignty over its own records.
8. **Closing summary**: each substrate property mapped to each Robodebt failure point, plus the honest limit (substrate enforces what is declared; the choice to declare is the operator's).

## What this verifies

This is the substrate's claim against a structurally different failure mode from Horizon. Where Horizon required attribution, content-addressing, and audit reachability, Robodebt requires structural enforcement of algorithmic preconditions and refusal-as-first-class-output. The substrate provides both, demonstrated end-to-end against a concrete claimant case that mirrors the actual Robodebt mechanism.

What it does not verify: the political question of whether an operator under cost pressure would actually deploy v2 rather than v1. The substrate makes the choice visible; whether the visibility produces accountability is downstream of the architecture.
