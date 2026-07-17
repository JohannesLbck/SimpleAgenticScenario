#!/usr/bin/ruby

require 'bundler/setup'
require "mcp"
require "riddl/client"
require "sinatra"

class Sleep < MCP::Tool
  description "Sleep a number of seconds"
  input_schema(
    properties: {
      timeout: { type: "integer", minimum: 1, maximum: 20},
    },
    required: ["timeout"]
  )

  class << self
    def call(timeout:, server_context:)
      srv = Riddl::Client.new('https://cpee.org/services/timeout.php')
      status, res = srv.post([
        Riddl::Parameter::Simple.new("timeout",timeout)
      ])
      if status >= 200 && status < 300
        res
      else
        #pp 'error'
      end
      result = res[0].value().read()

      MCP::Tool::Response.new([{
        type: "text",
        text: "Slept  #{timeout} seconds! (#{result})",
      }])
    end
  end
end

server = MCP::Server.new(
  name: "sleep_server",
  tools: [Sleep],
)

transport = MCP::Server::Transports::StreamableHTTPTransport.new(server,stateless: true)

set :port, 4568

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
