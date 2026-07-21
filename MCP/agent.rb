#!/bin/ruby

require 'bundler/setup'
require 'ruby_llm'
require "ruby_llm/mcp"
require 'json'
require 'riddl/client'

RubyLLM.configure do |config|
  config.anthropic_api_key = File.read(File.join(__dir__,'api.key'))
  config.anthropic_api_base = "https://morpheus.cit.tum.de/api"
  config.log_file = "./agent_logs/ruby_llm_#{ARGV[1]}.log"
  config.log_level = :debug
  config.request_timeout = 600
end

#anthropic_chat = RubyLLM.chat(model: 'mistralai/Ministral-3-14B-Reasoning-2512',provider: :anthropic,assume_model_exists: true)
#anthropic_chat = RubyLLM.chat(model: 'google/gemma-4-31B-it',provider: :anthropic,assume_model_exists: true)
#anthropic_chat = RubyLLM.chat(model: 'Qwen/Qwen3.6-27B',provider: :anthropic,assume_model_exists: true)
#anthropic_chat = RubyLLM.chat(model: 'qwen-35-35b-coding',provider: :anthropic,assume_model_exists: true)
if(ARGV[2].nil?()) then
  pp 'ARGV[2] is nil - fallback to "mistralai/Ministral-3-14B-Reasoning-2512"'
end
anthropic_chat = RubyLLM.chat(model: ARGV[2].nil?() ? 'mistralai/Ministral-3-14B-Reasoning-2512' : ARGV[2],provider: :anthropic,assume_model_exists: true)


RubyLLM::MCP.configure do |config|
  config.default_adapter = :ruby_llm
  #config.default_adapter = :mcp_sdk
end

light_client = RubyLLM::MCP.client(
  name: "light",
  adapter: :ruby_llm,
  transport_type: :streamable_http,
  config: {
    #url: "http://localhost:4567/_mcp",
    url: "https://power.bpm.cit.tum.de/light_mcp/_mcp",
  }
)

sleep_client = RubyLLM::MCP.client(
  name: "sleep",
  adapter: :ruby_llm,
  transport_type: :streamable_http,
  config: {
    #url: "http://localhost:4568/_mcp",
    url: "https://power.bpm.cit.tum.de/sleep_mcp/_mcp",
  }
)

log_client = RubyLLM::MCP.client(
  name: "log",
  adapter: :ruby_llm,
  transport_type: :streamable_http,
  config: {
    #url: "http://localhost:4569/_mcp",
    url: "https://power.bpm.cit.tum.de/log_mcp/_mcp",
  }
)

anthropic_chat.with_tools(*light_client.tools)
anthropic_chat.with_tools(*sleep_client.tools)
anthropic_chat.with_tools(*log_client.tools)

###### Set up logging
#srv = Riddl::Client.new('http://localhost:9091/log')
srv = Riddl::Client.new('https://power.bpm.cit.tum.de/log_behind_mcp/log')
status, res = srv.post([
  Riddl::Parameter::Simple.new("log_name",ARGV[1]),
])
if status >= 200 && status < 300
else
  pp 'set up of logging failed'
end
###### Set up logging


if(ARGV[0]) then
  prompt = ARGV[0]
  puts "-------------------------------prompt:-------------------------------\n#{prompt}"
  response = anthropic_chat.ask(prompt)
  puts "-------------------------------answer:-------------------------------\n#{response.content()}"
  File.write(File.join(__dir__,'agent_logs',"messages_#{ARGV[1]}.log"),anthropic_chat.messages().map() { |message| "[#{message.role().to_s().upcase()}] #{message.content().strip()}" }.to_json())
else
  puts "Provide the prompt as an argument!"
end

###### Redirect to standard log 
#srv = Riddl::Client.new('http://localhost:9091/log')
srv = Riddl::Client.new('https://power.bpm.cit.tum.de/log_behind_mcp/log')
status, res = srv.post([
  Riddl::Parameter::Simple.new("log_name","log"),
])
if status >= 200 && status < 300
else
  pp 'redirection to standard log failed'
end
###### Redirect to standard log 
