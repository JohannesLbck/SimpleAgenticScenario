#!/bin/ruby

require 'bundler/setup'
require 'optparse'

options = {
  time_sensitive: false,
}
OptionParser.new do |opts|
  opts.on("-t", "--time-sensitive", "enable time sensitive mode") do |f|
    options[:time_sensitive] = f
  end
end.parse!

#python3 EvalHelper/eval_agent_traceability.py --from '2026-07-13 21:38:06.541947935' --to '2026-07-13 21:43:46.723293031' MCP/logger/logs/oo1_run_0.log Simulators/simulator_static_oo1_oo3.log

next_start = false
next_end = false
start_ts = nil
end_ts = nil
counter = 0
lines = File.readlines(File.join(__dir__,ARGV[0]))
precisions = []
recalls = []
f_ones = []
lines.each() { |line|
  if(line =~ /Start/) then
    next_start = true
  elsif(line =~ /End/)
    next_end = true
  else
    if(next_start) then
      start_ts = line
      next_start = false
    elsif(next_end)
      end_ts = line
      next_end = false
    end
  end
  if(start_ts.nil?().!() && end_ts.nil?().!()) then
    puts "Iteration #{counter}"
    res = `python3 EvalHelper/eval_agent_traceability.py  --from '#{start_ts}' --to '#{end_ts}' MCP/logger/logs/#{ARGV[1]}_run_#{counter}.log Simulators/simulator_static_oo1_oo3.log`
    puts res
    res_lines = res.split("\n")
    res_lines.each() { |res_line|
      #pp res_line
      case res_line
      when /^Precision:.*(\d\.\d\d\d\d)/
        precisions.push($1.to_f())
      when /^Recall:.*(\d\.\d\d\d\d)/
        recalls.push($1.to_f())
      when /^F1 score:.*(\d\.\d\d\d\d)/
        f_ones.push($1.to_f())
      end
    }
    start_ts = nil
    end_ts = nil
    counter += 1
  end
}

puts "Precisions: #{precisions}"
mean_precision = precisions.sum().to_f() / precisions.length()
std_dev_precision = Math.sqrt(precisions.map { |x| (x - mean_precision) ** 2 }.sum() / precisions.length())
puts "Mean Precision: #{mean_precision}"
puts "Std. Dev. Precision: #{std_dev_precision}"

puts "Recalls: #{recalls}"
mean_recall = recalls.sum().to_f() / recalls.length()
std_dev_recall = Math.sqrt(recalls.map { |x| (x - mean_recall) ** 2 }.sum() / recalls.length())
puts "Mean Recall: #{mean_recall}"
puts "Std. Dev. Recall: #{std_dev_recall}"

puts "F1 scores: #{f_ones}"
mean_f_one = f_ones.sum().to_f() / f_ones.length()
std_dev_f_one = Math.sqrt(f_ones.map { |x| (x - mean_f_one) ** 2 }.sum() / f_ones.length())
puts "Mean F1: #{mean_f_one}"
puts "Std. Dev. F1: #{std_dev_f_one}"
