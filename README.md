# SSDP Release Notes Generator

**What it does**: Automatically creates weekly release notes by pulling data from JIRA and publishing beautiful reports to Confluence.

**Who uses it**: Release managers, project managers, and anyone who needs to track what went to production each week across APIM, EAH, DOCG, VDR, and PATRIC-SSDP.

## How to Use It

### Quick Start (3 Easy Steps)

1. **Open the App**: Go to the release notes web interface
2. **Enter Week Info**: Type the week number (like "45") and year (like "2026") 
3. **Click Generate**: The system does everything else automatically

![Main Interface Screenshot](screenshots/main-interface.png)
*Simple interface - just enter the week and click generate*

### What Happens Next

The system will:
- ✅ Check JIRA for anything that went to production that week
- ✅ Find all related issues (bugs, features, technical work)
- ✅ Create a formatted release notes page
- ✅ Publish it to Confluence
- ✅ Send email notifications (if set up)

![Release Notes Output Screenshot](screenshots/release-notes-output.png)
*Your finished release notes will look professional and include all the important details*

### Understanding the Results

Your release notes will show:

**📊 Release Summary Table** - Quick overview of what version of each component was released
![Component Summary Screenshot](screenshots/component-summary.png)

**📋 Detailed Issue Lists** - All the work that was included, organized by:
- 🚀 **FEATURES** - New functionality for users
- 🔧 **CODE** - Technical improvements and infrastructure work  
- 🐛 **BUGS** - Problem fixes and corrections

**🔗 Linked Issues** - Every related ticket, story, and task that was part of the release

## Configuration

### Required Environment Variables

```bash
# JIRA Authentication
JIRA_USERNAME=your-jira-email@stellantis.com
JIRA_API_TOKEN=your-jira-api-token

# Confluence Publishing
CONFLUENCE_USERNAME=your-confluence-email@stellantis.com
CONFLUENCE_API_TOKEN=your-confluence-api-token
CONFLUENCE_SPACE_KEY=pCPC
CONFLUENCE_PARENT_PAGE_ID=2831155954
```

**Confluence Page Location:**
- Space: `pCPC`
- Parent Page: [2026 - SSDP release notes](https://eng-stla.atlassian.net/wiki/spaces/pCPC/pages/2831155954/2026+-SSDP+release+notes)
- Child pages will be created under this parent page for each week

### Optional Email Notifications

```bash
# Email Configuration (Optional)
EMAIL_USERNAME=your-email@stellantis.com
EMAIL_PASSWORD=your-app-password
SMTP_SERVER=smtp.office365.com
SMTP_PORT=587
NOTIFICATION_RECIPIENTS=recipient1@stellantis.com,recipient2@stellantis.com
```

**Note**: 
- Email notifications are sent only for **first-time page generation**, not for updates
- If email configuration is missing, the system will continue to work without notifications
- Use app passwords for Office 365/Outlook authentication

## For Component Owners: How to Make Sure Your Releases Show Up

**If you own APIM, EAH, DOCG, VDR, or PATRIC-SSDP components, follow these simple rules so your releases are automatically included:**

### The Golden Rules

#### ✅ 1. Name Your JIRA Issues Correctly
- **APIM releases**: Issue summary should contain "APIM" and version info
- **EAH releases**: Issue summary should contain "EAH" and version info  
- **DOCG releases**: Start issue summary with "DOCG"
- **VDR releases**: Start issue summary with "VDR"
- **PATRIC-SSDP releases**: Start issue summary with "PATRIC-SSDP-"

#### ✅ 2. Update Status on the Right Day
- Move your issue to **"In production"** on the day you actually deploy
- For VDR: Move to **"Deploying to PROD"** on deployment day
- Don't change dates later - the system tracks when you first moved it

#### ✅ 3. Link Related Work
Connect your main release issue to:
- 🎯 User stories (becomes "FEATURES" in release notes)
- 🔧 Technical stories (becomes "CODE" in release notes)  
- 🐛 Bug fixes (becomes "BUGS" in release notes)

![Issue Linking Example Screenshot](screenshots/issue-linking.png)
*Link all related issues so they show up in the release notes*

### Common Mistakes to Avoid

❌ **"My release didn't show up!"**  
→ Check: Did you move it to "In production" during the right week?

❌ **"The release notes are missing details!"**  
→ Check: Did you link all the user stories, bugs, and technical work?

❌ **"Wrong version number in the report!"**  
→ Check: Is your JIRA issue summary formatted correctly with the version?

❌ **"It shows releases from the wrong week!"**  
→ Check: Don't change status transition dates after the fact

### Weekly Checklist for Component Owners

**Before Each Release:**
- [ ] JIRA issue is named with correct component prefix
- [ ] All related stories and bugs are linked to the main issue
- [ ] Issue descriptions are clear and user-friendly

**On Deployment Day:**
- [ ] Move main issue to production status immediately after deployment
- [ ] Verify the transition happened on the correct date
- [ ] Double-check that linked issues are still connected

**After Release Notes Are Published:**
- [ ] Review the generated notes for accuracy
- [ ] Report any missing or incorrect items to the release team

## Getting Started

### 🌐 Access the System
The SSDP Release Notes Generator is hosted and ready to use at:

**https://ssdp-release-notes.onrender.com/**

No installation or setup required - just open the link and start generating release notes!

## Troubleshooting

### "No releases found for this week"
**Most common causes:**
- No components were deployed to production that week (this is normal!)
- Issues weren't moved to "In production" status during the target week
- Issue names don't follow the naming conventions (missing APIM, DOCG, etc.)

**What to do:**
1. Check JIRA for issues that should have been released
2. Verify the status transition dates
3. Confirm issue naming follows the patterns above

### "Missing information in release notes" 
**Usually means:**
- Related issues aren't linked to the main release issue
- Issue descriptions are empty or unclear

**What to do:**
1. Check if all user stories, bugs, and technical work are linked
2. Ask component owners to improve issue descriptions

### "Can't publish to Confluence"
**Check:**
- Confluence credentials are correct
- Parent page exists and you have write permissions
- Network connection to Confluence is working

### "Email notifications not working"
**Check:**
- Email credentials are correct
- Using app password (not regular password) for Office 365
- SMTP server settings are right for your organization

## Need Help?

- **For missing releases**: Contact the component owner to fix JIRA issues
- **For technical problems**: Contact the SSDP development team  
- **For Confluence access**: Contact your Confluence administrator

## Files Created

When you run the system, it creates these files:
- `summary_output.html` - The release notes (also published to Confluence)
- `Linked_Issues_Report.txt` - Detailed breakdown of what was found
- `weekly_stopper.json` - Version history (used to track changes over time)

You don't need to do anything with these files - they're created automatically.
