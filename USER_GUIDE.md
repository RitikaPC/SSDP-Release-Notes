h1. 📘 SSDP Release Notes - User Guide

h2. Overview

The SSDP Release Notes Generator automatically creates professional weekly release documentation by pulling data from JIRA and publishing it to Confluence. This guide will help you understand what to do and how the system works.

----

h2. 🎯 What You Need To Do

h3. Step 1: Prepare Your JIRA Issues (Before Deployment)

*For Component Owners (APIM, EAH, DOCG, VDR, PATRIC-SSDP):*

# *Name your Enabler/Release issue correctly* in JIRA
#* Include the component name (e.g., "APIM", "DOCG", "VDR")
#* Include the correct version number in the summary
#* Example: {{APIM v3.5.2 Release}} or {{DOCG - Release v2.1.0}}
# *Link all related work* to your main Enabler issue:
#* User stories → will appear as "FEATURES" in release notes
#* Technical stories → will appear as "CODE" in release notes
#* Bug fixes → will appear as "BUGS" in release notes
# *Verify the Deploy Date* in your Enabler issue
#* Ensure it matches your actual planned deployment date
#* This date is used to organize releases by week

h3. Step 2: Update Status During Go/No-Go Meeting

*CRITICAL - This determines which week your release appears in:*

After your Go/No-Go meeting approves the deployment:

* *For most components*: Move your Enabler to *"In Production"* status
* *For VDR*: Move to *"Deploying to Prod"* status
* *Do this on the actual deployment day* - the system tracks when you first transition to this status

{warning}
*Important*: The date of this status transition determines which week's release notes will include your deployment. Don't backdate or change this later!
{warning}

h3. Step 3: Generate Release Notes

*For Release Managers:*

# Open the release notes generator: [https://ssdp-release-notes.onrender.com/|https://ssdp-release-notes.onrender.com/]
# *Enable pop-ups in your browser* (the results open in a new tab)
# Enter the release week information:
#* Week number (e.g., "45")
#* Year (optional, e.g., "2026")
# Click *"Generate & Open"*
# The system will:
#* Query JIRA for all components moved to production that week
#* Collect all linked issues (stories, bugs, technical work)
#* Generate formatted HTML release notes
#* Publish to Confluence automatically
#* Open the published page in a new tab

h3. Step 4: Review & Verify

After generation, verify that:
* ✅ All expected components are listed
* ✅ Version numbers are correct
* ✅ Related issues are included
* ✅ Deploy dates match actual deployment dates

----

h2. 📌 Important To-Dos (Please Read!)

h3. 🔓 Browser Settings
*The page opens in a new tab* - please ensure that *pop-ups are enabled* in your browser for the release notes site. Otherwise, you won't see your generated release notes.

h3. 📅 After Go/No-Go Meeting
*Make sure to update the Enabler workflow status correctly* when moving to:
* *"In Production"* (for APIM, EAH, DOCG, PATRIC-SSDP)
* *"Deploying to Prod"* (for VDR)

Do this *immediately after deployment* - the system uses the transition date to determine which week to include your release.

h3. 📆 Deploy Date Accuracy
*Ensure the Deploy Date is updated accurately* before generating the final release notes. This date appears in the published documentation and helps stakeholders understand the timeline.

h3. 🔢 Enabler Version
*Verify and enter the correct Enabler version* in your JIRA issue summary to avoid inconsistencies in the published page. Incorrect versions can confuse stakeholders and make tracking difficult.

----

h2. 💡 How It Works - Brief Example

h3. Example Scenario: APIM Release in Week 45, 2026

h4. Before Deployment (Monday)
*Component Owner (APIM Team):*
{noformat}
JIRA Issue: PCPC-12345
Summary: "APIM v3.5.2 Production Release"
Status: Ready for Deployment
Deploy Date: 2026-11-09 (Week 45)

Linked Issues:
├── PCPC-12301 (Story): "Add OAuth2 authentication support" 
├── PCPC-12302 (Story): "Implement rate limiting"
├── PCPC-12310 (Bug): "Fix timeout error in API gateway"
└── PCPC-12320 (Technical): "Upgrade to Java 17"
{noformat}

h4. During Go/No-Go Meeting (Wednesday, Nov 11)
*Release Manager approves deployment* ✅

*Component Owner immediately updates:*
{noformat}
JIRA Issue: PCPC-12345
Status: Ready for Deployment → In Production
[System records transition date: 2026-11-11]
{noformat}

h4. Friday - Generating Release Notes
*Release Manager:*
# Opens https://ssdp-release-notes.onrender.com/
# Enters Week: "45" and Year: "2026"
# Clicks "Generate & Open"

*System processes:*
{noformat}
🔍 Searching JIRA...
   → Found: APIM v3.5.2 (moved to Production on 2026-11-11)
   → Week 45 = Nov 9-15, 2026 ✅ Match!

🔗 Collecting linked issues...
   → PCPC-12301: OAuth2 authentication (Story → FEATURES)
   → PCPC-12302: Rate limiting (Story → FEATURES)  
   → PCPC-12310: Timeout fix (Bug → BUGS)
   → PCPC-12320: Java 17 upgrade (Technical → CODE)

📄 Generating release notes...
   → Component: APIM
   → Version: 3.5.2
   → Deploy Date: 2026-11-09
   → Status: In Production

📤 Publishing to Confluence...
   → Page created: "Week 45 - 2026 SSDP Release Notes"
   → URL: https://eng-stla.atlassian.net/wiki/spaces/pCPC/pages/...

✅ Success! Opening in new tab...
{noformat}

h4. Published Release Notes Look Like:
{noformat}
┌─────────────────────────────────────────────┐
│  Week 45 - 2026 SSDP Release Notes          │
│  November 9-15, 2026                        │
└─────────────────────────────────────────────┘

📊 RELEASE SUMMARY
┌──────────┬───────────┬──────────────┐
│ Component│ Version   │ Deploy Date  │
├──────────┼───────────┼──────────────┤
│ APIM     │ v3.5.2    │ 2026-11-09   │
└──────────┴───────────┴──────────────┘

🚀 FEATURES (2 items)
• [PCPC-12301] Add OAuth2 authentication support
• [PCPC-12302] Implement rate limiting

🔧 CODE (1 item)
• [PCPC-12320] Upgrade to Java 17

🐛 BUGS (1 item)
• [PCPC-12310] Fix timeout error in API gateway
{noformat}

----

h2. ❌ Common Mistakes to Avoid

h3. Mistake #1: Forgetting to Update Status
*Problem:* APIM deployed on Wednesday, but status wasn't updated until Friday.
*Result:* Release appears in the wrong week's notes.
*Solution:* Update status immediately after Go/No-Go approval.

h3. Mistake #2: Wrong Version in Issue Summary
*Problem:* JIRA says "APIM Release" but actual version deployed was v3.5.2
*Result:* Release notes show incomplete version information.
*Solution:* Always include version number in the Enabler issue summary.

h3. Mistake #3: Not Linking Related Work
*Problem:* Main release issue exists, but related stories aren't linked.
*Result:* Release notes only show the main issue, missing all features and bugs.
*Solution:* Link all user stories, bugs, and technical work to the main Enabler.

h3. Mistake #4: Pop-ups Blocked
*Problem:* Click "Generate & Open" but nothing happens.
*Result:* Release notes were created, but you can't see them.
*Solution:* Enable pop-ups in browser settings for the release notes domain.

h3. Mistake #5: Incorrect Deploy Date
*Problem:* Deploy Date field shows old date or wrong date.
*Result:* Confusing timeline in published notes.
*Solution:* Always update Deploy Date to match actual deployment before generating notes.

----

h2. 🆘 Troubleshooting

|| Problem || Solution ||
| *"My release didn't show up"* | Check that you moved to "In Production" during the target week |
| *"Wrong version number"* | Update the Enabler issue summary to include correct version |
| *"Missing features/bugs"* | Link all related issues to your main Enabler issue |
| *"Page won't open"* | Enable pop-ups in your browser |
| *"No releases found"* | Verify issues were moved to production status during that week |

----

h2. 📞 Need Help?

* *Missing releases?* → Contact the component owner to check JIRA
* *Technical issues?* → Contact SSDP development team
* *Questions about workflow?* → Contact your Release Manager

----

h2. ✅ Quick Checklist

*Before Deployment:*
* ( ) Enabler issue summary includes component name and version
* ( ) All user stories, bugs, and technical work are linked
* ( ) Deploy Date field is accurate

*During/After Go/No-Go:*
* ( ) Move Enabler to "In Production" / "Deploying to Prod" immediately
* ( ) Verify status transition happened on correct date

*Generating Notes:*
* ( ) Pop-ups enabled in browser
* ( ) Correct week and year entered
* ( ) Review published page for accuracy

*After Publishing:*
* ( ) All expected components included
* ( ) Version numbers correct
* ( ) Linked issues appear properly
* ( ) Deploy dates accurate

----

_Last updated: February 2026_
