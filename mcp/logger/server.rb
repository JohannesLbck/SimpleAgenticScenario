#!/usr/bin/ruby
require 'riddl/server'
require 'psych'
require 'json'


class LogEvent < Riddl::Implementation
  def response
    log = @a[0]
    label = @p[0].value()
    lifecycle = @p[1].value()
    parameters = JSON.parse(@p[2].value().read())
    event = {'event' => {'concept:name' => label, 'lifecycle:transition' => lifecycle, 'data' => parameters.map() { |key,value| {'name' => key, 'value' => value} },'time:timestamp' => Time.now()}}
    pp event
    #File.write('log.log','a',flags:(File::CREAT | File::APPEND))
    #File.write('log.log',"#{event.to_yaml()}",mode:'a+')
    pp log
    File.write("#{log[:name]}.log","#{event.to_yaml()}",mode:'a+')
    return 
  end
end

class NewLog < Riddl::Implementation
  def response
    log = @a[0]
    pp log[:name]
    log[:name] = @p[0].value()
    pp log[:name]
    return 
  end
end



Riddl::Server.new(File.dirname(__FILE__) + '/description.xml', :bind => '::1',  :port => 9091) do
  accessible_description true
  #cross_site_xhr true
  cross_site_xhr false

  log = {:name => 'log'}

  on resource do
    on 'log' do
      run LogEvent,log if post 'event'
      run NewLog,log if post 'new_log' 
    end
  end
end.loop!
