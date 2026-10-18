# Test fixtures

`timetable.html` and `multi-semester.html` contain fictional groups, lessons,
instructors, and room names. The multi-semester fixture models the source
markup needed to test selected-calendar filtering. Its dates cover more than
one term, so these tests catch lessons leaking across semesters without using
any real class schedule.

Tests can create summer and empty-calendar variants from the synthetic fixture
entirely offline. No timetable page has been captured for the test suite.
