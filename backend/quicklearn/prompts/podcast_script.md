{#
  Podcast scripting (PRD §7.6). Runs on the `deep` model.

  The two-host format is not decoration. Host B asking the question a confused student would
  ask is what makes the format pedagogically useful rather than just a summary read aloud —
  it surfaces the step that the notes glossed over.

  Length is enforced by word count in the prompt because TTS duration tracks words closely and
  the user picked a target length expecting it to be roughly honoured.
#}
Write a two-host podcast script explaining this material.

**Host A** teaches. Knows the subject, explains clearly, uses concrete examples.
**Host B** is the intelligent student. Asks the question a listener would actually have, pushes
back when something is glossed over, and occasionally restates an idea to check understanding.

Target: **about {{ target_words }} words** total, for roughly {{ target_minutes }} minutes of
audio. Treat this as a real constraint.

Rules:

- Open with Host A naming what will be covered. No "welcome back to the podcast", no fictional
  show name, no sponsor read.
- Alternate speakers naturally. Host B should not interject after every single line, and should
  not be a pure yes-man — the questions must be ones a confused person would genuinely ask.
- Cover the material's actual substance in its own order of dependency. This is a study aid, so
  accuracy outranks entertainment.
- Spell out mathematics as spoken words. Write "lambda equals two" rather than "$\lambda = 2$",
  and "the determinant of A minus lambda I" rather than the symbols. This gets read aloud by a
  speech engine that cannot pronounce notation.
- No stage directions, no sound effects, no "[laughs]". Only spoken words.
- Close with Host A giving a short recap of the key points. No sign-off pleasantries.
- Each line should be one speaker's continuous turn — a sentence or a short paragraph, not a
  monologue.

## Material

{{ content }}
