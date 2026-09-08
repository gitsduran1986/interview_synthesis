---
expert: Expert 1
role: Senior Director, IT Infrastructure and Operations at Pharma Company (2025 - present)
platform: ServiceNow
section: Current Environment
section_slug: 02-current-environment
source: raw/Synquery FULL-transcript_ITSM Test_Expert1.docx
---

# Current Environment — Expert 1

*Senior Director, IT Infrastructure and Operations at Pharma Company (2025 - present)*

---
**AI Interviewer** (00:01:33)

Thanks for sharing that, Travis—that's helpful. Let's start with your time at your previous employer where you used ServiceNow. Can you walk me through how that organization used the platform and which specific modules you deployed?

**Expert 1** (00:01:53)

Yeah, I used ServiceNow at my previous stop, Thermo Fisher. It was a very large implementation—about a $40B company with roughly 2,000 sites. We handled around 2M tickets annually. The modules we used included Incident, Request, Problem, Change, and Knowledge. We had a full Configuration Management Database (CMDB), Asset Management, and Major Incident Management. We also used HR—many HR service requests ran through ServiceNow. And we started implementing GRC as another module.

**AI Interviewer** (00:02:59)

That's a very extensive deployment. What were the key drivers that led Thermo Fisher to adopt ServiceNow in the first place?

**Expert 1** (00:03:10)

The key drivers were faster resolution of IT tickets and reducing MTTR. We were on, I believe, Zendesk at the time, and needed better, faster performance and improved routing. We also needed centralized management of all IT assets. So, multiple drivers, but really it was about improving service levels (SLAs), MTTR, and the user experience from an IT perspective.

**AI Interviewer** (00:04:10)

You mentioned improving service levels and user experience. Were those goals part of a broader strategic initiative, like a digital transformation or an effort to consolidate fragmented IT tools across the organization?

**Expert 1** (00:04:23)

Can you repeat the question?

**AI Interviewer** (00:04:29)

Of course. I was asking if the goals of improving service levels and user experience were part of a larger company initiative, such as a digital transformation program or an effort to bring different IT tools together onto one platform.

**Expert 1** (00:04:43)

Yes, it was about consolidating a lot of the IT tools—some of it was even paper-based. We wanted to improve service levels and MTTR and also be able to track our assets. In addition, we wanted better compliance—we’re subject to heavy regulations such as SOC, ISO, GxP, and SOC 2. ServiceNow can help with controls around our IT assets and equipment, so that was another driver.

**AI Interviewer** (00:05:31)

You’ve already mentioned expanding into asset management and HR Service Delivery. What were the specific business needs that drove you to adopt those modules? You also mentioned implementing GRSD. Could you clarify which module that was?

**Expert 1** (00:05:46)

The module was GRC—Governance, Risk, and Compliance. Basically, you can load all of your risks. For example, if you haven’t done a pen test on a server in a while, that’s a risk because the system hasn’t been stress-tested for security. That would be logged in the GRC module as a risk, then categorized and prioritized. And could you repeat the other part of your question?

**AI Interviewer** (00:06:23)

Thanks for clarifying GRC. My other question was about the drivers for expanding into asset management and HR Service Delivery. What specific business needs led to those decisions?

**Expert 1** (00:06:36)

For asset management, we needed better inventory of our assets to support lifecycle management. For HR, they were handling a lot of tickets via phone, paper, and email, so we needed a consolidated system to improve service delivery and tracking from an HR perspective. Those were two big drivers.

**AI Interviewer** (00:07:07)

That makes sense. Was the decision to expand into modules like asset management, HR, and GRC driven more by a top-down, enterprise-wide strategy, or did it come from specific departments identifying pain points and requesting a solution?

**Expert 1** (00:07:24)

The pain points really started at the site and division levels, and then it became a strategic digital transformation initiative. There were a lot of complaints about slowness—in both the process and service levels—which weren’t great. This was observed at the lower levels, typically at the site level, the manufacturing plants. They have a site lead and an IT partner. From there, you go up to the divisions, where you have a division lead. Once we started seeing commonality across sites and divisions, it moved up to a strategic initiative from an IT global perspective. So, it started at the site level, grew into a strategic IT initiative, and then was implemented globally.

**AI Interviewer** (00:08:24)

I’d like to follow up on a few things you’ve mentioned. That’s a very clear explanation of bottom-up demand driving a top-down strategic decision. First, you said you’re currently using a custom ITSM at Abzena, which is a big shift from the large-scale ServiceNow environment at Thermo Fisher. What makes a custom solution the right fit for Abzena today instead of a commercial platform?

**Expert 1** (00:08:55)

Great question. I’ve been with Abzena for about 6 months, and that decision was made by my predecessor. We actually had ServiceNow at one point, but they felt that for the size of the company it was too complicated for end users. They also wanted to take advantage of AI, so this custom solution integrates with Microsoft Teams—you can talk to it, and it can create a ticket. It has AI capabilities to recognize an IT issue, try some troubleshooting, and if it can’t resolve it, it creates a ticket. At that time, ServiceNow’s agents weren’t as advanced, and this was more simplified. The backend is also easier to manage. Personally, I’m not sure why they made the move—I think it was poorly implemented and then they decided to switch. Not my decision; it predated me and we have to live with it. One repercussion is we don’t really have asset management for all our IT assets, which is a big pain point for me right now. We’re now looking at possibly implementing just the ServiceNow asset management piece to categorize all our GMP equipment and IT assets. I don’t think the decision was well thought through and it doesn’t scale, but for the company size at that time, I guess it made sense to simplify the user experience.

**AI Interviewer** (00:11:00)

That’s a fascinating situation, dealing with the consequences of a predecessor’s decision. It sounds like you’re trying to find a hybrid approach to fill the gaps. Let’s go back to Thermo Fisher for a moment. You mentioned that GxP and SOX compliance were major drivers for adopting ServiceNow over tools like Zendesk. What specific capabilities did ServiceNow have for compliance that Zendesk lacked? Was it about having better audit trails, or were there specific workflow controls that were essential?

**Expert 1** (00:11:34)

Could you repeat the question?

**AI Interviewer** (00:11:40)

Certainly. What specific compliance features did ServiceNow offer for GxP and SOX that your previous tool, Zendesk, was missing?

**Expert 1** (00:11:51)

Yes. Number one, the system was validated. For example, when you enter a change, you can check a box indicating it’s GxP-related. If it’s GxP-related, it triggers specific workflows for routing and the change process that Zendesk did not have. It provided more robust functionality for a typical GxP-related change. Typically, you have an IT change that then goes to Quality; Quality opens a change, it gets approved and reviewed, you might have to do testing, etc. ServiceNow offered more customization and out-of-the-box functionality than Zendesk, including advanced routing and workflows to accommodate GxP processes.
