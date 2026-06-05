require 'bundler/setup'
require "mcp"
require "riddl/client"
require "sinatra"


class EnvironmentStatus < MCP::Tool
  description "Get status of environment (ambient brightness in lux, current brightness of the light in lumen, number of occupants, if motion is detected, and the current simulated time)"

  class << self
    def call(server_context:)
      #pp "light_status"
      srv = Riddl::Client.new('https://power.bpm.cit.tum.de/simulator/readsensor')
      status, res = srv.get([])
      if status >= 200 && status < 300
        res
      else
        #pp 'error when checking light status'
      end
      result = JSON.parse(res[0].value().read())
      pp result

      MCP::Tool::Response.new([{
        type: "text",
        #text: "Ambient brightness is #{result['ambient_light_lux']} lux! Motion is #{result['motion_detected'] ? '' : 'not '}detected!",
        text: "Ambient brightness is #{result['ambient_light_lux']} lux! The current brightness of the light is #{result['current_light_lumen']}! There are #{result['occupancy_count']} people in the room! Motion is #{result['motion_detected'] ? '' : 'not '}detected! The simulated time is currently #{result['dataset_timestamp'].split('T').last()}!",
        #text: "Ambient brightness is #{result['ambient_light_lux']} lux! Motion is #{result['motion_detected'] ? '' : 'not '}detected! (#{result})",
      }])
    end
  end
end

=begin
class LightStatus < MCP::Tool
  description "Get value of lightbulb brightness in lumen"

  class << self
    def call(server_context:)
      #pp "light_status"
      srv = Riddl::Client.new('https://power.bpm.cit.tum.de/simulator/changelumens/state')
      status, res = srv.get([])
      if status >= 200 && status < 300
        res
      else
        #pp 'error when checking light status'
      end
      result = JSON.parse(res[0].value().read())
      pp result

      MCP::Tool::Response.new([{
        type: "text",
        #text: "Brightness is #{result['current_light_lumen']} lumen! (#{result})",
        text: "Brightness is #{result['current_light_lumen']} lumen!",
      }])
    end
  end
end
=end

class ChangeLumen < MCP::Tool
  description "Sets the lumen for the light bulb"
  input_schema(
    properties: {
      lumen: { type: "number", minimum: 0, maximum: 5000 },
    },
    required: ["lumen"]
  )

  class << self
    def call(lumen:, server_context:)
      #pp "light_set (to #{mode})"
      srv = Riddl::Client.new('https://power.bpm.cit.tum.de/simulator/changelumens')
      status, res = srv.put([
        Riddl::Parameter::Simple.new("lumen",lumen)
      ])
      if status >= 200 && status < 300
        res
      else
        #pp 'error when setting light'
      end
      result = res[0].value().read()

      MCP::Tool::Response.new([{
        type: "text",
        text: "Light is now set to #{lumen} lumen!",
        #text: "Light is now set to #{lumen} lumen! (#{result})",
      }])
    end
  end
end


server = MCP::Server.new(
  name: "light_server",
  #tools: [EnvironmentStatus,LightStatus,ChangeLumen],
  tools: [EnvironmentStatus,ChangeLumen],
)

transport = MCP::Server::Transports::StreamableHTTPTransport.new(server,stateless: true)

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
  pp body
  [status,headers,body]
end
