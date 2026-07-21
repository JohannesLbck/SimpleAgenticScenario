#!/bin/ruby

#require 'bundler/setup'
require 'optparse'

options = {
  time_sensitive: false,
}
OptionParser.new do |opts|
  opts.on("-t", "--time-sensitive", "enable time sensitive mode") do |f|
    options[:time_sensitive] = f
  end
end.parse!

#python3 EvalHelper/eval_traceability.py --start-timestamp '1784128764' --end-timestamp '1784128770' EvalHelper/cpee_logs/gt.xes.yaml Simulators/simulator_static.log

start_ts = nil
end_ts = nil
counter = 0
times = nil
eval("times=#{ARGV[0]}")

start_ts = times.first()

xes_lumens = []
xes_sensors = []
sensor_lumens = []
sensor_sensors = []
times.each_with_index() { |time,index|
  if(index != 0 && index % 72 == 0) then
    end_ts = time
  end
  if(start_ts.nil?().!() && end_ts.nil?().!()) then
    puts "Iteration #{counter}"
    res = `python3 EvalHelper/eval_traceability.py --start-timestamp '#{start_ts}' --end-timestamp '#{end_ts}' EvalHelper/cpee_logs/#{ARGV[1]}.xes.yaml Simulators/simulator_static.log`
    puts res
    res_lines = res.split("\n")
    res_lines.each() { |res_line|
      #pp res_line
      case res_line
      when /^Xes_Log: \(Lumen, Sensor\) \((\d+), (\d+)\)/
        xes_lumens.push($1.to_i())
        xes_sensors.push($2.to_i())
      when /^Sensor_Log: \(Lumen, Sensor\) \((\d+), (\d+)\)/
        sensor_lumens.push($1.to_i())
        sensor_sensors.push($2.to_i())
      end
    }
    start_ts = end_ts 
    end_ts = nil
    counter += 1
  end
}

#diff_lumens = sensor_lumens.zip(xes_lumens).map() { |a, b| b.to_f()/a }
#diff_sensors = sensor_sensors.zip(xes_sensors).map() { |a, b| b.to_f()/a }
diff_lumens = sensor_lumens.zip(xes_lumens).map() { |a, b| a-b }
diff_sensors = sensor_sensors.zip(xes_sensors).map() { |a, b| a-b }

puts "XES_Lumens: #{xes_lumens}"
mean_xes_lumen = xes_lumens.sum().to_f() / xes_lumens.length()
std_dev_xes_lumen = Math.sqrt(xes_lumens.map { |x| (x - mean_xes_lumen) ** 2 }.sum() / xes_lumens.length())
puts "Mean XES_Lumens: #{mean_xes_lumen}"
puts "Std. Dev. XES_Lumens: #{std_dev_xes_lumen}"

puts "XES_Sensors: #{xes_sensors}"
mean_xes_sensor = xes_sensors.sum().to_f() / xes_sensors.length()
std_dev_xes_sensor = Math.sqrt(xes_sensors.map { |x| (x - mean_xes_sensor) ** 2 }.sum() / xes_sensors.length())
puts "Mean XES_Sensors: #{mean_xes_sensor}"
puts "Std. Dev. XES_Sensors: #{std_dev_xes_sensor}"

puts "Sensor_Lumens: #{sensor_lumens}"
mean_sensor_lumen = sensor_lumens.sum().to_f() / sensor_lumens.length()
std_dev_sensor_lumen = Math.sqrt(sensor_lumens.map { |x| (x - mean_sensor_lumen) ** 2 }.sum() / sensor_lumens.length())
puts "Mean Sensor_Lumens: #{mean_sensor_lumen}"
puts "Std. Dev. Sensor_Lumens: #{std_dev_sensor_lumen}"

puts "Sensor_Sensors: #{sensor_sensors}"
mean_sensor_sensor = sensor_sensors.sum().to_f() / sensor_sensors.length()
std_dev_sensor_sensor = Math.sqrt(sensor_sensors.map { |x| (x - mean_sensor_sensor) ** 2 }.sum() / sensor_sensors.length())
puts "Mean Sensor_Sensors: #{mean_sensor_sensor}"
puts "Std. Dev. Sensor_Sensors: #{std_dev_sensor_sensor}"

puts "Diff_Lumens: #{diff_lumens}"
mean_diff_lumen = diff_lumens.sum().to_f() / diff_lumens.length()
std_dev_diff_lumen = Math.sqrt(diff_lumens.map { |x| (x - mean_diff_lumen) ** 2 }.sum() / diff_lumens.length())
puts "Mean Sensor_Sensors: #{mean_diff_lumen}"
puts "Std. Dev. Sensor_Sensors: #{std_dev_diff_lumen}"

puts "Diff_sensors: #{diff_sensors}"
mean_diff_sensor = diff_sensors.sum().to_f() / diff_sensors.length()
std_dev_diff_sensor = Math.sqrt(diff_sensors.map { |x| (x - mean_diff_sensor) ** 2 }.sum() / diff_sensors.length())
puts "Mean Sensor_Sensors: #{mean_diff_sensor}"
puts "Std. Dev. Sensor_Sensors: #{std_dev_diff_sensor}"

puts "Result Lumens:#{mean_xes_lumen}(+/-)#{std_dev_xes_lumen}/#{mean_sensor_lumen}(+/-)#{std_dev_sensor_lumen} -> #{mean_diff_lumen}(+/-)#{std_dev_diff_lumen}"

puts "Result Sensors: #{mean_xes_sensor}(+/-)#{std_dev_xes_sensor}/#{mean_sensor_sensor}(+/-)#{std_dev_sensor_sensor} -> #{mean_diff_sensor}(+/-)#{std_dev_diff_sensor}"
