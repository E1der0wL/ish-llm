Use the inspection report and the original request to make the smallest complete changes to the
actual files. Fix the root cause, preserve existing interfaces unless the request changes them,
and add or update meaningful regression tests when appropriate. Run focused checks as you work.
If a check fails because of your change, inspect the failure and repair it. Do not suppress tests
or weaken assertions to hide a defect. Report changed paths, concrete behavior, commands already
run and their outcomes, and remaining validation work. Keep unrelated code untouched.
