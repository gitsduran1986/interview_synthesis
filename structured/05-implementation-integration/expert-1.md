---
expert: Expert 1
role: Senior Director, IT Infrastructure and Operations at Pharma Company (2025 - present)
platform: ServiceNow
section: Implementation & Integration
section_slug: 05-implementation-integration
source: raw/Synquery FULL-transcript_ITSM Test_Expert1.docx
---

# Implementation & Integration — Expert 1

*Senior Director, IT Infrastructure and Operations at Pharma Company (2025 - present)*

---
**AI Interviewer** (00:33:05)

That’s a very efficient team for such a large deployment. It sounds like you used a blended model of internal staff for day-to-day work and partners for heavy lifting, which helped protect the overall ROI. Let’s move on to the implementation itself. What was your implementation experience like at Thermo Fisher, and what were the biggest challenges you faced?

**Expert 1** (00:33:28)

Great question. The implementation was quite complex and took roughly 9-12 months to fully complete. The biggest pain points were lacking a clear definition of done—there was so much customization that scope boundaries were fuzzy—and underestimating the internal effort required. User testing wasn’t clearly defined, so we had to pull in resources who weren’t expecting it, and it was hard to secure their time.

**AI Interviewer** (00:34:23)

So, a 9-12 month timeline. The key challenges were a lack of a clear vision for done and underestimating the internal resource commitment needed for things like user acceptance testing. How well did ServiceNow integrate with your other major enterprise systems, like Microsoft Azure, SAP, and your various security tools?

**Expert 1** (00:34:45)

It integrated well, but it took time to build. We set up single sign-on with Active Directory and integrated Workday. It wasn’t plug-and-play; our engineers and identity access management team had to do quite a bit of work. There was a lot of back-and-forth on APIs, customization, changes on both sides, and permissions.

**AI Interviewer** (00:35:23)

So the integrations worked well in the end, but they required significant custom development and coordination between teams rather than simple out-of-the-box connectors. Did you use ServiceNow’s Integration Hub for any of this, or was it primarily custom API work?

**Expert 1** (00:35:39)

No—at the time that wasn’t available, so it was mostly custom work using direct APIs.

**AI Interviewer** (00:35:53)

I’d like to follow up on a couple of points. You rated ServiceNow’s integration capabilities a 9 out of 10 earlier, but you also mentioned that the integrations took time and involved a lot of custom work. Specifically for your security tools and SAP integrations, did you have to rely on custom development, or were there any out-of-the-box connectors that made it easier?

**Expert 1** (00:36:15)

They had some out-of-the-box APIs, but we had legacy systems and tools that those APIs wouldn’t connect to, so we built a middle layer to make them talk. For example, we wanted to bring in Databricks data related to certain service tickets. There was a Databricks connector, but a lot of our legacy ERP data was feeding Databricks and the connector didn’t have access to it, so we wrote a custom API to integrate the two.

**AI Interviewer** (00:37:03)

That's a great example of how legacy systems can complicate modern integrations. You also mentioned that a lack of a clear vision for “done” was a major implementation challenge. In your experience, is that ambiguity typically a failure of the implementation partner to set clear boundaries, or is it inherent to a platform as customizable as ServiceNow, where scope creep is almost inevitable?

**Expert 1** (00:37:27)

Great question. I think it was a combination of both—working with ServiceNow and an integrator. There should be some commonality between ServiceNow’s preferred playbook and what partners are doing. Somewhere between how ServiceNow used to run implementations and how partners operate today, there are gaps. I think there’s misalignment between ServiceNow and the partner ecosystem on how long things really take. Partners need to be clearer about the level of effort required and what the implementation plan looks like. For a concrete example, we expected a 6-7 month implementation, and it ended up taking 9-12 months—roughly another quarter we had to absorb.
