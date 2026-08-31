# Create your assistant personality

Copy the example configuration before editing it; the real file is ignored by
Git. The `assistant.personality` block is the durable description of how your
assistant should sound and behave. Write instructions, not a biography copied
from private conversations.

A useful personality usually specifies:

- tone and conversational pace;
- how concise or exploratory replies should be;
- values and boundaries;
- when to challenge assumptions or ask a question;
- phrases, habits, and role-play styles to avoid.

Example:

```yaml
assistant:
  name: "Marko"
  personality: |-
    You are a calm, perceptive creative partner. Lead with the useful answer.
    Prefer plain language and short spoken replies. Be honest about uncertainty.
    Help turn vague ideas into one concrete next action without becoming pushy.
```

Change one trait at a time and try the same three prompts after each edit. This
makes it easier to hear what the instruction changed. Never commit your real
configuration if it contains personal details.
