# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

   - Employee `emp_code`: Stops the same employee code from being added twice.
   - Attendance `(emp_code, date)`: Stops two records for the same worker on the same day. Also helps find their monthly records.
   - Attendance `(date descending, emp_code ascending)`: Helps list newest dates first, with employee codes in order for each date.
   - Attendance `status`: Helps find records with a given status.
   - Employee `department`: Helps find workers in a department.
   - Employee `joined_on`: Helps find workers who joined by a given date.
   - Employee `(department, joined_on)`: Helps count workers in a department on a given date.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

   - First, both requests find the employee.
   - Next, both work out the punch time, attendance date and late minutes.
   - Then, both try to save a record.
   - MongoDB saves one record. The unique `(emp_code, date)` index stops the other record from being saved.
   - The saved request returns 201. The other gets a duplicate-key error, which the code turns into 409.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

   Both employees are returned because equal late-minute totals get the same rank. For example, ranks can be 1, 2, 2, 4. With a limit of 2, all three employees at ranks 1 and 2 are included. The limit checks rank, so it does not cut off one of the tied employees. Tied employees are shown in employee-code order.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

   The summary starts with employees who joined by the end of the month. It then looks for their attendance records and keeps employees even when no records are found. Each employee adds one to the department's headcount, so workers with zero attendance records still count.

5. **One thing you would change** if this had to serve 100x the data.

   I would save monthly totals for each employee so reports do not calculate everything again each time. These totals would be updated when attendance changes. The original records would stay available for daily reports and history.
