"""Low-attendance email notifications.

A sweep over recorded attendance that finds students below a configured
percentage in a class they are enrolled in, and emails each one a
plain-text summary of the classes they are short in.

This package is a threshold comparison and nothing more. There is no
model, no training, and no prediction anywhere in it -- the number it
compares against the bar is the same figure the student's own dashboard
already shows them (see attendance/summary.py). CLAUDE.md rules the
academic-risk model out of scope permanently; nothing here is a stand-in
for it.

Deliberately reachable only from the `notify-low-attendance` CLI command.
No route, no blueprint, and no React screen exists for it, so nothing on
a request path can trigger mail. Nothing outside this package imports
`smtplib` or `email`, and nothing in it imports from `routes/` or from
`recognition/` -- the sweep runs on a machine where neither CV library is
installed.
"""
