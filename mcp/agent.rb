#!/bin/ruby

require 'ruby_llm'
require "ruby_llm/mcp"

RubyLLM.configure do |config|
  config.anthropic_api_key = File.read(File.join(__dir__,'api.key'))
  config.anthropic_api_base = "https://morpheus.cit.tum.de/api"
  config.log_file = './ruby_llm.log'
  config.log_level = :debug
  config.request_timeout = 600
end

#anthropic_chat = RubyLLM.chat(model: 'mistralai/Ministral-3-14B-Reasoning-2512',provider: :anthropic,assume_model_exists: true)
anthropic_chat = RubyLLM.chat(model: 'google/gemma-4-31B-it',provider: :anthropic,assume_model_exists: true)
#anthropic_chat = RubyLLM.chat(model: 'Qwen/Qwen3.6-35B-A3B',provider: :anthropic,assume_model_exists: true)
#anthropic_chat = RubyLLM.chat(model: 'qwen-35-35b-coding',provider: :anthropic,assume_model_exists: true)


RubyLLM::MCP.configure do |config|
  config.default_adapter = :ruby_llm
  #config.default_adapter = :mcp_sdk
end

light_client = RubyLLM::MCP.client(
  name: "light",
  adapter: :ruby_llm,
  transport_type: :streamable_http,
  config: {
    url: "http://localhost:4567/_mcp",
  }
)

sleep_client = RubyLLM::MCP.client(
  name: "sleep",
  adapter: :ruby_llm,
  transport_type: :streamable_http,
  config: {
    url: "http://localhost:4568/_mcp",
  }
)

log_client = RubyLLM::MCP.client(
  name: "log",
  adapter: :ruby_llm,
  transport_type: :streamable_http,
  config: {
    url: "http://localhost:4569/_mcp",
  }
)

anthropic_chat.with_tools(*light_client.tools)
anthropic_chat.with_tools(*sleep_client.tools)
anthropic_chat.with_tools(*log_client.tools)

#anthropic_chat.after_message do |_|
#  pp "after message"
#  instruction = anthropic_chat.messages().shift()
#  while(anthropic_chat.messages().size() >= 10)
#    pp "clean messages"
#    anthropic_chat.messages().shift()
#  end
#  anthropic_chat.messages.unshift(instruction)
#end

if(ARGV[0]) then
  prompt = ARGV[0]
  puts "-------------------------------prompt:-------------------------------\n#{prompt}"
  response = anthropic_chat.ask(prompt)
  puts "-------------------------------answer:-------------------------------\n#{response.content()}"
else
  puts "Provide the prompt as an argument!"
end


