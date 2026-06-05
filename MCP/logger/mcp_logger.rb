require 'bundler/setup'
require "mcp"
require "riddl/client"
require "sinatra"

class LogEvent < MCP::Tool
  description "Log an event"
  input_schema(
    properties: {
      # label of the event
      label: { type: "string" },
      # lifecycle of the event (either "start" or "complet")
      #lifecycle: { type: "string", properties: {"data": {"enum": ["start","end"]}}},
      lifecycle: { type: "string", enum: ["start","complete"] },
      # data parameters used in the event (provided in json format)
      parameters: { type: "object" }
    },
    required: ["label","lifecycle","parameters"]
  )

  class << self
    def call(label:, lifecycle:, parameters:, server_context:)
      pp parameters
      srv = Riddl::Client.new('http://localhost:9091/log')
      status, res = srv.post([
        Riddl::Parameter::Simple.new("label",label),
        Riddl::Parameter::Simple.new("lifecycle",lifecycle),
        Riddl::Parameter::Complex.new("parameters","application/json",parameters.to_json())
      ])
      if status >= 200 && status < 300
        res
      else
        #pp 'error'
      end

      MCP::Tool::Response.new([{
        type: "text",
        text: "Event #{label}/#{lifecycle} with parameters #{parameters.to_json()} logged!",
      }])
    end
  end
end

server = MCP::Server.new(
  name: "logging_server",
  tools: [LogEvent],
)

transport = MCP::Server::Transports::StreamableHTTPTransport.new(server,stateless: true)

set :port, 4569

get '/_mcp' do
  data = JSON.parse request.body.read
  pp data
  request.body.rewind
  status, headers, body = transport.handle_request(request)
end

post '/_mcp' do
  data = JSON.parse request.body.read
  pp data
  request.body.rewind
  status, headers, body = transport.handle_request(request)
end
