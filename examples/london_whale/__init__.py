"""
Modelling the JPMorgan Chase 'London Whale' synthetic credit derivatives
loss of 2012 against the substrate's drift detection, content-addressed
model recalibration, and structural escalation commitments.

Between January and April 2012, the Chief Investment Office (CIO) of
JPMorgan Chase built a synthetic credit derivatives portfolio whose
loss eventually reached approximately USD 6.2 billion. The trades were
concentrated in the credit-default-swap index CDX.NA.IG.9 and related
instruments and were associated with one trader (Bruno Iksil, nicknamed
'the London Whale' for the size of his positions). The losses became
public in May 2012; the Senate Permanent Subcommittee on Investigations
published its report 'JPMorgan Chase Whale Trades' in March 2013.

The substrate-relevant failure points (drawn from the Senate report and
subsequent regulatory findings):

1. The CIO recalibrated its Value-at-Risk (VaR) model in late 2011 to
   a 'new VaR model' that produced substantially lower risk figures
   than the prior model on the same portfolio. The recalibration was
   not properly validated. The new model's outputs systematically
   underestimated portfolio risk relative to actual P&L volatility.
2. Risk limits were breached repeatedly; the response was often to
   adjust the model (or the limit) rather than the position.
3. Escalation to senior management was delayed and dampened. Jamie
   Dimon's 'tempest in a teapot' comment in April 2012 reflected, in
   part, that the information reaching the CEO was filtered through
   layers that downplayed the situation.
4. Refer-to-human as a decision was not a structural output of the
   risk system. There was no point at which the substrate of the
   trading process could refuse to authorise further position-taking;
   the controls were procedural and overridable.

The substrate's commitments map to each:

1. CONTENT-ADDRESSED MODEL RECALIBRATION. Replacing a VaR model with a
   recalibrated version produces a new content_id for the model unit.
   Every risk figure on the ledger carries the model content_id that
   produced it. The substitution is structurally visible; it cannot be
   done quietly.

2. DRIFT DETECTION ON BEHAVIOUR-CHARACTERISED RISK MODELS. The VaR
   model's spec declares calibration criteria: the relationship
   between model-predicted volatility and actual reported P&L
   volatility must stay within bounds over a window. Systematic
   divergence trips drift; subsequent invocations of the drifted
   model refuse until a reset is authorised by a credential outside
   the desk that owns the model.

3. ESCALATION AS A STRUCTURAL CREDENTIAL. Authorising positions beyond
   a declared limit requires invoking an escalation policy bound to
   the senior-risk-officer credential. The trading desk cannot
   self-issue the escalation. A position attempt that exceeds the
   limit without escalation is refused at submission; the refusal is
   the refer-to-human signal.

4. CROSS-OPERATOR REGULATOR AUDIT. The OCC (Office of the Comptroller
   of the Currency), as an audit operator under a cooperative
   substrate, can invoke audit_positions on the bank's substrate. The
   audit produces a forensic report on the regulator's ledger. The
   record cannot be unilaterally amended.

This demonstration runs JPMorgan (single operator with desk, risk, and
senior-risk credentials in its delegation chain) and the OCC (regulator,
cross-operator audit). Two VaR models: v1 (calibrated) and v2 (the
recalibrated 'new VaR model' that systematically underestimates risk).
Scenarios: a routine position under v1; a recalibration to v2 (a
visible event); v2 used to size positions; drift on v2 trips after a
few observations of model-vs-actual divergence; a position attempt
exceeding the limit without escalation refused; OCC cross-operator
audit produces the forensic record.

Run with: python -m examples.london_whale.run
"""
