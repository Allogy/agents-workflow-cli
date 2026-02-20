# Jira Ticket Template (BDD-Based)

## Overview

This template ensures all tickets are created with clear, testable acceptance criteria using Behavior-Driven Development (BDD) principles. It helps both humans and bots create consistent, high-quality tickets.

---

## Template Structure

### Title

```
Clear description of the behavior
```

**Examples:**
- `User can validate workflow syntax offline`
- `CLI returns helpful error for missing API key`

---

### Epic Title (Naming Convention for Epics Only)

**Note:** This naming convention applies ONLY to Epics, not to individual tickets/stories.

```
[Market] | [Audience] | [Feature title + version number]
```

**Format:** `ORGANIC or ENTERPRISE or O&E | ADMIN or BUILDER or END USER | Feature title + version number`

**Market options:** ORGANIC, ENTERPRISE, O&E

**Audience definitions:**
- **ADMIN**: For the administrator or general matters necessary and inherent to the platform
- **BUILDER**: Features aimed at those who will be building the workflows
- **END USER**: Those who will benefit from the workflow (ideally, these features should be suggested more by customers, as they are the ones who know best what they need)

**Example Epic titles:**
- `ORGANIC | BUILDER | Workflow CLI Templating System v1.0`
- `ENTERPRISE | ADMIN | Multi-Environment Deployment Tools v2.1`
- `O&E | BUILDER | Advanced Workflow Validation v1.5`

---

### User Story / Problem Statement

**For Features:**
```
As a [role/user type]
I want to [action/capability]
So that [business value/outcome]
```

**For Bugs:**
```
When [user action/context]
The system [incorrect behavior]
Expected: [correct behavior]
```

---

### Situation Report

Current state and context - what's happening now?

- **Current behavior**: What exists today?
- **Impact**: Who's affected? How critical?
- **Business context**: Why does this matter now?

---

### Acceptance Criteria (BDD Scenarios)

```gherkin
Scenario 1: [Main happy path]
Given [initial context/state]
When [action/trigger]
Then [expected outcome]
And [additional outcome if needed]

Scenario 2: [Edge case or alternative path]
Given [different context]
When [action]
Then [expected behavior]
```

**Additional Criteria:**
- [ ] [Any non-functional requirements: performance, security, etc.]
- [ ] [Documentation updated]
- [ ] [Tests written]

---

### Technical Notes (Optional)

- **Dependencies**:
- **Affected components**:
- **API changes needed**:
- **CLI commands affected**:

---

### Definition of Done

- [ ] Code implements all acceptance scenarios
- [ ] Unit tests pass (BDD scenarios covered)
- [ ] Code reviewed and approved
- [ ] Tested in [environment]
- [ ] Documentation updated

---

## Example Tickets

### Example 1: Feature Ticket

**Title:** `Workflow validation reports line numbers for errors`

**User Story:**
```
As a workflow developer
I want to see exact line numbers when validation fails
So that I can quickly locate and fix errors in my workflow YAML
```

**Situation Report:**
- **Current behavior**: Validation errors show field names but not line numbers
- **Impact**: Developers spend 5-10 minutes searching large YAML files for errors
- **Business context**: Complex workflows can be 500+ lines, making error hunting tedious

**Acceptance Criteria:**

```gherkin
Scenario: Validation error shows line number
Given I have a workflow file with an invalid node type on line 42
When I run "workflow validate myfile.yaml"
Then I see an error message with "line 42"
And the error describes what's invalid

Scenario: Multiple errors show all line numbers
Given I have a workflow with 3 validation errors
When I run "workflow validate myfile.yaml"
Then I see all 3 errors with their respective line numbers
And errors are sorted by line number
```

**Additional Criteria:**
- [ ] Line numbers are accurate (1-indexed)
- [ ] Works with workflows up to 10,000 lines
- [ ] Column numbers shown for complex errors

**Technical Notes:**
- Use ruamel.yaml for line number preservation
- Update ValidationError to include line/column info
- Format: "Error at line X, column Y: [message]"

**Definition of Done:**
- [ ] Code implements all acceptance scenarios
- [ ] Unit tests pass (BDD scenarios covered)
- [ ] Code reviewed and approved
- [ ] Tested with real workflows
- [ ] Documentation updated with examples

---

### Example 2: Bug Ticket

**Title:** `Push command fails silently when lockfile is corrupted`

**Problem Statement:**
```
When pushing a workflow with a corrupted .workflow.lock file
The CLI exits with success code 0 but workflow is not deployed
Expected: Should show clear error and exit with non-zero code
```

**Situation Report:**
- **Current behavior**: Silent failure, no error message, exit code 0
- **Impact**: Users think deployment succeeded when it didn't
- **Business context**: CI/CD pipelines don't catch the failure

**Acceptance Criteria:**

```gherkin
Scenario: Corrupted lockfile shows error
Given I have a .workflow.lock file with invalid JSON
When I run "workflow push myfile.yaml"
Then I see an error message about the corrupted lockfile
And the command exits with code 1

Scenario: Missing lockfile UUID shows warning
Given my lockfile has a missing UUID for a node
When I run "workflow push myfile.yaml"
Then I see a warning about the missing UUID
And the command continues with API lookup
```

**Additional Criteria:**
- [ ] Exit code 1 for all error conditions
- [ ] Clear error messages suggest fixes
- [ ] Existing valid lockfiles unaffected

**Technical Notes:**
- Add try/except around lockfile JSON parsing
- Validate lockfile schema on load
- Use proper exit codes: 0=success, 1=error, 2=invalid input

**Definition of Done:**
- [ ] Code implements all acceptance scenarios
- [ ] Unit tests pass (BDD scenarios covered)
- [ ] Code reviewed and approved
- [ ] Tested with various corrupted lockfiles
- [ ] No regression in existing push functionality

---

## Tips for Using This Template

1. **Be specific with scenarios** - Each scenario should be testable
2. **Include edge cases** - Think about what could go wrong
3. **Keep it user-focused** - Write from the user's perspective
4. **Make acceptance criteria measurable** - Avoid vague terms like "fast" or "good"
5. **Update as you learn** - If requirements change during development, update the ticket

---

*Last updated: 2026-02-04*
*Project: RAG (Agents Platform) - Workflow CLI*