---
expert: Expert 2
role: Director, IT Service Management at Life Sciences Firm(2021 - present)
platform: BMC Helix
section: Implementation & Integration
section_slug: 05-implementation-integration
source: raw/Synquery_FULL-transcript_ITSM Test_Expert2.docx
---

# Implementation & Integration — Expert 2

*Director, IT Service Management at Life Sciences Firm(2021 - present)*

---
**AI Interviewer** (00:13:10)

Let's talk about implementation. What was your experience rolling out BMC Helix, and what were the biggest challenges?

**Expert 2** (00:13:20)

It took about seven months from kickoff to go-live, which was faster than I expected going in. The biggest challenge was data migration - pulling asset and ticket history out of the legacy ManageEngine tool. The export tools weren't great, so we ended up doing a lot of manual cleanup and mapping before we could load it into the new CMDB. The second challenge was change management with end users - a lot of site-level staff had used the old portal for years and were resistant to a new interface.

**AI Interviewer** (00:14:05)

How well did BMC Helix integrate with your other core systems, SAP and Workday specifically?

**Expert 2** (00:14:14)

Workday integration was fairly smooth - BMC had a solid connector for HR data sync, onboarding and offboarding trigger workflows. SAP was harder. We use SAP for some inventory and equipment data that we wanted reflected in the CMDB, and there wasn't an out-of-the-box connector that fit our SAP configuration, so our internal team built a custom API integration. That took an extra six or seven weeks we hadn't originally budgeted.

**AI Interviewer** (00:15:00)

Did you use any of BMC's integration hub tooling, or was it mostly custom API work?

**Expert 2** (00:15:08)

A mix. For Workday it was mostly BMC's native connector. For SAP it was custom REST API work built by our integration engineer, because our SAP instance is fairly customized on the inventory side.

**AI Interviewer** (00:15:30)

You mentioned end-user resistance to the new interface. How did that manifest, and how did you address it?

**Expert 2** (00:15:39)

Mostly complaints about the portal looking different and people not knowing where to submit certain request types. We ran a series of lunch-and-learns at each site and recorded short walkthrough videos. It took about two months post-go-live before ticket volume through the correct channels stabilized.
