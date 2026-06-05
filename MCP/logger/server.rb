#!/usr/bin/ruby
require 'riddl/server'
require 'psych'
require 'json'


class LogEvent < Riddl::Implementation
  def response
=begin
    mode_value = @p[0].value()
    pp mode_value
    mode = mode_value == 'true' ? true : false
    light_status = @a[0]
    pp "old: #{light_status}"
    light_status[:status] = mode ? 1.0 : 0.0
    pp "new: #{light_status}"
    pp "return: #{{:light => light_status}.to_json()}}"
=end
    label = @p[0].value()
    lifecycle = @p[1].value()
    parameters = JSON.parse(@p[2].value().read())
    event = {'event' => {'concept:name' => label, 'lifecycle:transition' => lifecycle, 'data' => parameters.map() { |key,value| {'name' => key, 'value' => value} },'time:timestamp' => Time.now()}}
    pp event
    #File.write('log.log','a',flags:(File::CREAT | File::APPEND))
    File.write('log.log',"#{event.to_yaml()}",mode:'a+')
    return 
  end
end

Riddl::Server.new(File.dirname(__FILE__) + '/description.xml', :bind => '::1',  :port => 9091) do
  accessible_description true
  #cross_site_xhr true
  cross_site_xhr false

  light_status = {:status => 0.0}

  on resource do
    on 'log' do
      run LogEvent if post 'event'
    end
  end
end.loop!
