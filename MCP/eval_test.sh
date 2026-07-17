for i in {0..1}; do
  echo "This is iteration $i"
  echo "Start"
  date
  ./agent.rb 'Adjust the lighting based on the following rules: Do not change the lumen of the lamp unless any of the following rules hold: (1) If there is user input react to user input, (2) If the occupancy is less than 1 then lower lumen to 0, (3) In the night (23-6) if there is movement aim for 50 lux, (4) In the night (23-6) if there is no movement lower lumen to 0, (5) In the morning (6-9) if occupancy is greater or equal 1 then aim for 100-200 lux, (6) during midday (9-14) if occupancy is greater or equal 1 then aim for 300-500 lux, (7) In the afternoon (14-18) if occupancy is greater or equal 1 aim for 200-300 lux, and (8) In the evening (18-23) if occupancy is greater or equal 1 then aim for 200-300 lux! Make sure this is done for the next 72 seconds which corresponds to 12 hours in simulated time (check for the conditions every second - i.e., every 10 minutes in simulated time)! Log the tools you use along with their parameters (apart from the logging tool itself)!' "test_run_$i" 'qwen-35-35b-coding'
  echo "End"
  date
done
