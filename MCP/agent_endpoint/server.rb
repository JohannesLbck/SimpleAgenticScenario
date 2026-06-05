#!/usr/bin/ruby
require 'riddl/server'
require 'json'
require 'ruby_llm'
require "ruby_llm/mcp"


class LLMCall < Riddl::Implementation
  def response
    pp @p

    RubyLLM.configure do |config|
      config.anthropic_api_key = File.read(File.join(__dir__,'api.key'))
      config.anthropic_api_base = "https://morpheus.cit.tum.de/api"
      config.log_file = './ruby_llm.log'
      config.log_level = :debug
      config.request_timeout = 600
    end
    
    model = @p[1].value()
    anthropic_chat = RubyLLM.chat(model: model,provider: :anthropic,assume_model_exists: true)
    #anthropic_chat = RubyLLM.chat(model: 'mistralai/Ministral-3-14B-Reasoning-2512',provider: :anthropic,assume_model_exists: true)
    #anthropic_chat = RubyLLM.chat(model: 'google/gemma-4-31B-it',provider: :anthropic,assume_model_exists: true)
    #anthropic_chat = RubyLLM.chat(model: 'Qwen/Qwen3.6-35B-A3B',provider: :anthropic,assume_model_exists: true)
    #anthropic_chat = RubyLLM.chat(model: 'qwen-35-35b-coding',provider: :anthropic,assume_model_exists: true)
    
    RubyLLM::MCP.configure do |config|
      config.default_adapter = :ruby_llm
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
    #prompt = "hello"
    prompt = @p[0].value()

    callback = @h['CPEE_CALLBACK']
    pp callback
    if(callback.nil?().!())
      Thread.new(prompt,callback) { |prompt,callback| 
        resp = anthropic_chat.ask(prompt)
        Riddl::Client.new(callback).put [
          Riddl::Parameter::Complex.new("json","application/json",{:response => resp.content()}.to_json)
        ]
      }
      @headers.push(Riddl::Header.new('CPEE_CALLBACK','true'))
      return Riddl::Parameter::Complex.new("json","application/json",{:response => "please wait"}.to_json)
    else
      resp = anthropic_chat.ask(prompt)
      return Riddl::Parameter::Complex.new("json","application/json",{:response => resp.content()}.to_json)
    end
  end
end

Riddl::Server.new(File.dirname(__FILE__) + '/description.xml', :bind => '127.0.0.1',  :port => 9092) do
  accessible_description true
  #cross_site_xhr true
  cross_site_xhr false

  on resource do
    on resource 'llm_call' do
      run LLMCall if post 'llm_call'
    end
  end
end.loop!
