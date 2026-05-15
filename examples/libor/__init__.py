"""
Modelling the LIBOR (London Interbank Offered Rate) manipulation scandal
of 2005-2012 against the substrate's content-addressed submissions,
cooperative-substrate aggregation, and drift detection commitments.

Between 2005 and 2012, traders at multiple panel banks (Barclays, UBS,
RBS, Deutsche Bank, Citigroup, JP Morgan, others) colluded to submit
LIBOR rates that did not reflect their actual interbank borrowing
costs. Two distinct manipulation patterns occurred: during normal
market conditions, traders submitted rates to profit their banks'
derivatives positions; during the 2007-2008 financial crisis, banks
submitted artificially low rates to disguise funding stress.

The British Bankers' Association (BBA) collected daily submissions from
16-18 panel banks. The "trim and average" methodology was supposed to be
robust to individual manipulation; it failed because the manipulation
was systemic. Fines exceeded USD 9 billion across the major panel
banks (2012-2015). Tom Hayes (UBS / Citigroup) was convicted in 2015.
LIBOR has been phased out (2021-2023) in favour of alternative
reference rates anchored to actual transactions.

The substrate-relevant failure points:

1. Submissions were not structurally connected to underlying activity.
   A bank could submit any rate it chose; the administrator had no
   mechanism to verify the rate against the bank's actual money-market
   activity.
2. Aggregation was not a cooperative-substrate event. The administrator
   produced the benchmark; panel banks accepted the published rate;
   no joint witnessing.
3. No structural divergence detection between submitted rates and
   underlying activity, either at submission time or over a window.
4. The regulator (FSA / FCA / CFTC) had no structural audit access;
   investigations happened only after suspicion arose through
   whistleblowing or pattern detection in regulators' own data.

The substrate's commitments map to each:

1. CONTENT-ADDRESSED SUBMISSIONS. Each submission is a ledger event on
   the submitting bank's substrate, signed by the submitter credential,
   carrying declared money-market activity context for the window.
2. COOPERATIVE-SUBSTRATE AGGREGATION. The `aggregate_libor` unit runs on
   the administrator's substrate but is witnessed under a cooperative
   custodian that includes all panel banks and the administrator. No
   single party can produce a valid benchmark without the others'
   signatures.
3. POINT-IN-TIME DIVERGENCE POLICY. A submission whose rate diverges
   from the implied midpoint of declared activity by more than a
   threshold is refused at submission time. DRIFT DETECTION on the
   submit_rate unit catches systematic divergence over a window even
   when individual submissions pass the per-submission threshold.
4. CROSS-OPERATOR REGULATOR AUDIT. The FCA, as an audit operator under
   a regulator cooperative substrate, can invoke `audit_submissions` on
   any panel bank. The forensic report is on the regulator's ledger;
   neither side can deny the audit happened.

This demonstration runs three panel banks (BankA, BankB, BankC), an
administrator (LIBORAdmin), and a regulator (FCA) federated under two
cooperative substrates (panel + admin for aggregation; banks + admin +
FCA for audit). Shows: an honest aggregation round; a manipulation
attempt refused by the divergence policy at submission time; a more
sophisticated systematic-bias pattern caught by drift detection over a
window; a regulator cross-operator audit producing the forensic
record.

Run with: python -m examples.libor.run
"""
