---
title: "need help with form checking thing"
repo: https://github.com/teerev/dft-orch
clone_branch: main
push_branch: aos/validator-lib2
max_iterations: 5
context_files: []

# Quality gates
min_assertions: 10
coverage_threshold: 85
min_tests: 8
max_test_failures: 0
require_hypothesis: false
require_type_check: true
shell_policy: warn

acceptance_commands:
  - "pytest -q"
---

# hi, need some help

ok so im making this website thing and users keep putting garbage in my forms and it breaks everything. like someone put their phone number in the email box?? and then my code crashed lol

i need something that checks if the stuff they type in is actually valid before i try to use it. like a filter or checker or whatever you call it

## what im trying to do

so like when someone fills out a signup form i want to check:
- did they actually put something in the required boxes (not just leave it blank)
- is the email actually an email and not just random letters
- is their age a number and is it reasonable (like between 18 and 120 or whatever, nobody is 500 years old lol)
- username should be at least a few characters, cant just be "a"

the annoying thing is right now when something is wrong i only find out about ONE thing at a time. so they fix that, submit again, then theres ANOTHER error. and they have to keep doing that. i want to just tell them everything thats wrong all at once you know?

## how i kinda want it to work

i dont really know python that well but something like

```
checker = ValidationThing()  # or whatever its called
checker.add_check("email", EmailCheck())
checker.add_check("age", NumberBetween(18, 120))
# etc

problems = checker.run(what_the_user_typed)
if problems:
    show_errors(problems)
```

something like that?? idk if thats how you do it in python. the important thing is i can add different checks to different fields and then run it all at once

oh also it would be cool if i could do like checker.add_check(...).add_check(...) on one line but thats not super important

## the checks i need

**required** - they have to fill this in. but like, empty is different from not there at all right? if they just hit space thats still "something" i think? actually wait no spaces dont count. hmm actually maybe empty string is ok but None isnt?? idk you figure it out

**email** - looks like an email. has the @ thing and a dot after it. doesnt need to be super strict just catch the obvious bad ones

**length stuff** - minimum and maximum characters. oh but only for text obviously, if its a number that doesnt make sense

**number range** - between X and Y. for like ages and stuff. should probably work for decimals too in case i need it for prices later

**pattern matching** - sometimes i need weird specific formats, can i just give you a regex? is that what its called? the thing with the slashes and stars

**type checking** - make sure its actually text or actually a number, before checking other stuff

## other stuff

oh yeah if a field isnt required and they just dont fill it in at all, dont yell at them about it failing the other checks. like if age is optional and they skip it, dont be like "age must be between 18 and 120" thats dumb

also if someone puts text where a number should go dont just crash, tell them nicely whats wrong

um what else... oh the result thing should have like a yes/no for "did it pass" and also the list of what went wrong. organized by which field had the problem

put it in validator.py i guess

lmk if u have questions, sorry if this doesnt make sense i can try to explain better
