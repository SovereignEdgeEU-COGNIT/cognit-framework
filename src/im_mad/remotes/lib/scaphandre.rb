#!/usr/bin/env ruby

# -------------------------------------------------------------------------- #
# Copyright 2002-2025, OpenNebula Project, OpenNebula Systems                #
#                                                                            #
# Licensed under the Apache License, Version 2.0 (the "License"); you may    #
# not use this file except in compliance with the License. You may obtain    #
# a copy of the License at                                                   #
#                                                                            #
# http://www.apache.org/licenses/LICENSE-2.0                                 #
#                                                                            #
# Unless required by applicable law or agreed to in writing, software        #
# distributed under the License is distributed on an "AS IS" BASIS,          #
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.   #
# See the License for the specific language governing permissions and        #
# limitations under the License.                                             #
#--------------------------------------------------------------------------- #

require 'json'
require 'yaml'
require 'open3'

#
# Scaphandre power monitoring via direct binary execution
#
# This class executes the scaphandre binary with the JSON exporter to collect
# power consumption metrics for the host and individual VMs (QEMU processes).
#
class ScaphandreClient

    VM_ID_PATTERN = /one-(\d+)/.freeze

    attr_reader :metrics, :config

    def initialize(config)
        @config = config
        @metrics = nil
    end

    #
    # Get the total Host power consumption in microwatts
    #
    # @return [String] Host Power Consumption in microwatts, empty string if unavailable
    #
    def host_power
        pull_metrics if @metrics.nil?
        return '' if @metrics.nil?

        sample = @metrics.is_a?(Array) ? @metrics.last : @metrics
        return '' if sample.nil? || !sample.is_a?(Hash)

        host = sample['host'] || sample[:host]
        return '' if host.nil?
        host = host.is_a?(Array) ? host.first : host
        return '' unless host.is_a?(Hash)

        consumption = host['consumption'] || host[:consumption]
        return '' if consumption.nil?

        consumption.to_s
    rescue StandardError => _e
        STDERR.puts "Scaphandre host_power error: #{_e.message}"
        ''
    end

    #
    # Get the power consumption of each VM managed by OpenNebula.
    # Filters consumers by regex (one-<id>) on cmdline/exe; scaphandre is run
    # without --process-regex so we get full JSON and filter here.
    #
    # @return [Hash] A map of VM ID => power consumption (microwatts)
    #
    def vms_power
        pull_metrics if @metrics.nil?
        return {} if @metrics.nil?

        sample = @metrics.is_a?(Array) ? @metrics.last : @metrics
        return {} if sample.nil? || !sample.is_a?(Hash) || sample['consumers'].nil?

        vms_power = {}
        sample['consumers'].each do |consumer|
            # OpenNebula VMs: cmdline or exe contains one-<vm_id>
            cmdline = consumer['cmdline'] || consumer['exe'] || ''
            match = cmdline.match(VM_ID_PATTERN)
            next unless match

            vm_id = match[1]
            vms_power[vm_id] = consumer['consumption'].to_s
        end

        vms_power
    rescue StandardError => _e
        STDERR.puts "Scaphandre vms_power error: #{_e.message}"
        {}
    end

    #
    # Execute scaphandre binary and parse JSON output from stdout.
    # Uses --max-top-consumers and --process-regex to limit output to OpenNebula VMs.
    # Never raises; returns nil on any failure so monitoring probes are not affected.
    #
    # @return [Hash] Parsed metrics or nil on failure
    #
    def pull_metrics
        cmd = build_command
        stdout, stderr, status = Open3.capture3(cmd)

        unless status.success?
            STDERR.puts "Scaphandre execution failed: #{stderr}" if stderr && !stderr.empty?
            return nil
        end

        # Scaphandre writes 2 header lines to stdout:
        #   "Scaphandre json exporter"
        #   "Sending ⚡ metrics"
        # Then the JSON as a single line.
        lines = stdout.strip.split("\n")
        json_line = lines.drop(2).join("\n")
        @metrics = json_line.empty? ? nil : JSON.parse(json_line)
        @metrics
    rescue StandardError => _e
        STDERR.puts "Scaphandre pull_metrics error: #{_e.message}"
        @metrics = nil
    end

    private

    def build_command
        cmd = @config[:require_sudo] ? 'sudo ' : ''
        cmd += "#{@config[:path]} json"
        cmd += " -t #{@config[:timeout]}"
        cmd += " --max-top-consumers #{@config[:max_consumers]}"
        cmd += " --process-regex '#{@config[:process_regex]}'"

        cmd
    end

end

#
# Configuration-aware monitor wrapper
#
# Loads configuration from power.conf and provides a high-level interface
# for power monitoring operations used by the OpenNebula monitoring probes.
#
class ScaphandreMonitor

    CONF_PATH = "#{__dir__}/../../etc/im/kvm-probes.d/power.conf"

    DEFAULT_CONF = {
        :binary => {
            :path => '/usr/bin/scaphandre',
            :timeout => 4,
            :max_consumers => 50,
            :process_regex => 'qemu-kvm-one-\\d+',
            :require_sudo => false
        },
        :metrics => {
            :vm => false,
            :host => false
        }
    }.freeze

    attr_reader :client

    def initialize
        @conf = load_config
        @client = ScaphandreClient.new(@conf[:binary])
    rescue StandardError => _e
        STDERR.puts "ScaphandreMonitor init error: #{_e.message}"
        @conf = DEFAULT_CONF.dup
        @client = nil
    end

    #
    # Get the total Host power consumption
    #
    # @return [Float] Host Power Consumption in microwatts, nil on error
    #
    def host_power
        return nil if @client.nil?

        result = client.host_power
        return nil if result.nil? || result.to_s.strip.empty?

        result.to_f
    rescue StandardError => e
        STDERR.puts "Error getting host power: #{e.message}"
        nil
    end

    #
    # Get the power consumption of each VM
    #
    # @return [Hash] A map of VM ID => power consumption (float, microwatts)
    #
    def vms_power
        return {} if @client.nil?

        result = client.vms_power
        result.transform_values(&:to_f)
    rescue StandardError => e
        STDERR.puts "Error getting VMs power: #{e.message}"
        {}
    end

    #
    # Pull metrics from scaphandre
    #
    # @return [Hash] Parsed metrics or nil on failure
    #
    def pull_metrics
        return nil if @client.nil?

        client.pull_metrics
    rescue StandardError => e
        STDERR.puts "Error pulling metrics: #{e.message}"
        nil
    end

    #
    # Check if monitoring is enabled for a specific metric type
    #
    # @param metric [String] 'vm' or 'host'
    # @return [Boolean] true if monitoring is enabled
    #
    def monitoring_enabled?(metric)
        return false if @conf.nil? || @conf[:metrics].nil?

        @conf[:metrics][metric.to_sym]
    end

    private

    def load_config
        if File.exist?(CONF_PATH)
            user_conf = YAML.load_file(CONF_PATH)
            deep_merge(DEFAULT_CONF.dup, user_conf)
        else
            DEFAULT_CONF.dup
        end
    end

    def deep_merge(base, overlay)
        base.merge(overlay) do |_key, oldval, newval|
            if oldval.is_a?(Hash) && newval.is_a?(Hash)
                deep_merge(oldval, newval)
            else
                newval
            end
        end
    end

end
