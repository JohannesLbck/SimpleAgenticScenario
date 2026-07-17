for i in {0..9}; do
  echo "This is iteration $i"
  echo "Start"
  date +'%Y-%m-%d %H:%M:%S.%N'
  ./agent.rb 'Adjust the lighting based on standard guidelines for brightness during day/night and sleep/being awake. Also consider that the light should be turned on whenever it is dark and I want to go somewhere! Make sure this is done for the next 72 seconds which corresponds to 12 hours in simulated time (check for the conditions every second - i.e., every 10 minutes in simulated time)! Log the tools you use along with their parameters (apart from the logging tool itself)!' "oo1_qwen_run_$i" 'Qwen/Qwen3.6-27B'
  echo "End"
  date +'%Y-%m-%d %H:%M:%S.%N'
done
