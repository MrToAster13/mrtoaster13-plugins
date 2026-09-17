# Spec: tailored resumes

The only domain file a batch needs. `{id}`, `{jd}`, `{resume}` and any other key
in the unit are substituted before the prompt reaches the agent.

## Worker

Tailor a resume for the job posting at `{jd}`, starting from `{resume}`.

Fact base: `<project>/master-resume.md`. Every line of the
output must trace to something in that file. You may reword, reorder, and cut.
You may not add a skill, tool, employer, date, or certification that is not
already there, even when the posting clearly wants it.

Format rules: one page, no Summary section.

Write the result to `<project>/resumes/out/{id}.md` and return
that path.

## Verifier

You are checking a tailored resume for `{id}` against the job posting at `{jd}`.
You did not write it and you owe it nothing.

Find a reason to reject it. Work through these in order and stop at the first
real hit:

1. Open `<project>/master-resume.md`. Take every concrete claim
   in the tailored resume (employer, title, date range, tool, certification,
   metric) and find it in the master. Anything you cannot locate is a fail, and
   the reason must quote the invented text.
2. A Summary section is a fail.
3. More than one page of content is a fail.
4. A claim that is technically present in the master but stretched past what it
   says is a fail. "Exposure to Splunk" becoming "Splunk administration" counts.

Return ok=true only if you worked through all four and found nothing. If you
could not read a file you needed, return ok=false and say which file.
