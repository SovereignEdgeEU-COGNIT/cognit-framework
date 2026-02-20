#!/usr/bin/env ruby

ONE_LOCATION = ENV['ONE_LOCATION']

if !ONE_LOCATION
    RUBY_LIB_LOCATION = '/usr/lib/one/ruby'
    GEMS_LOCATION     = '/usr/share/one/gems'
else
    RUBY_LIB_LOCATION = ONE_LOCATION + '/lib/ruby'
    GEMS_LOCATION     = ONE_LOCATION + '/share/gems'
end

if File.directory?(GEMS_LOCATION)
    real_gems_path = File.realpath(GEMS_LOCATION)
    if !defined?(Gem) || Gem.path != [real_gems_path]
        $LOAD_PATH.reject! {|l| l =~ /vendor_ruby/ }
        require 'rubygems'
        Gem.use_paths(real_gems_path)
    end
end

$LOAD_PATH << RUBY_LIB_LOCATION

require 'base64'
require 'rexml/document'
require 'ipaddr'
require 'open3'
require 'json'
require 'net/http'
require 'uri'
require 'yaml'
require 'time'

require 'geocoder'
require 'opennebula'
include OpenNebula

ELECTRICITY_MAPS_CONF = '/etc/one/electricity_maps.conf'

def public_ip?(ip)
    ip_obj = IPAddr.new(ip)

    # Check if IPv4 address
    if ip_obj.ipv4?
        private_ranges = [
            IPAddr.new('10.0.0.0/8'),
            IPAddr.new('172.16.0.0/12'),
            IPAddr.new('192.168.0.0/16'),
            IPAddr.new('127.0.0.1/8')
        ]
    # Check if IPv6 address
    elsif ip_obj.ipv6?
        private_ranges = [
            IPAddr.new('fc00::/7'),       # Unique local address
            IPAddr.new('fe80::/10'),      # Link-local address
            IPAddr.new('::1'),            # Loopback address
            IPAddr.new('::ffff:0:0/96'),  # IPv4-mapped IPv6 addresses
            IPAddr.new('::/96')           # IPv4-compatible IPv6 addresses
        ]
    else
        return false # Not a valid IP address
    end

    # Check if the IP address is in private ranges
    private_ranges.none? {|range| range.include?(ip_obj) }
end

one_auth = File.read("#{Dir.home}/.one/one_auth").chomp
client = Client.new(one_auth, 'http://localhost:2633/RPC2')

xml = REXML::Document.new(Base64.decode64(ARGV[0]))

host = Host.new_with_id(xml.elements['HOST/ID'].text, client)

rc = host.info
raise rc.message if OpenNebula.is_error?(rc)

#################################################################

cmd = "ssh #{host.name} ip --json -brief address show"
ips, e, s = Open3.capture3(cmd)

raise e if s != 0

public_ips = []

JSON.parse(ips).each do |ip|
    next if ip['addr_info'].empty? || !ip['addr_info'][0].key?('local')

    address = ip['addr_info'][0]['local']

    public_ips << address if public_ip?(address)
end

if public_ips.empty?
    puts "No coordinates detected for host #{host.name}"
    exit 0
end

geolocations = []

public_ips.each do |ip|
    coordinates = Geocoder.coordinates(ip)
    coordinates = "#{coordinates.first},#{coordinates.last}"

    geolocations << coordinates unless geolocations.include?(coordinates)
end

geolocation = "GEOLOCATION=\"#{geolocations.join(' ')}\""

#################################################################

if !geolocation.empty?
    rc = host.update(geolocation, true)
    raise rc.message if OpenNebula.is_error?(rc)
    puts "Host #{host.name} geolocation set to: #{geolocations.join(' ')}"
else
    puts "No coordinates detected for host #{host.name}"
    exit 0
end

#################################################################
# Cluster centroid: compute from all hosts in the cluster
#################################################################

cluster_id = host['CLUSTER_ID'].to_i

cluster = Cluster.new_with_id(cluster_id, client)
rc = cluster.info
raise rc.message if OpenNebula.is_error?(rc)

host_ids = []
cluster.host_ids.each { |hid| host_ids << hid }

lats = []
lons = []

host_ids.each do |hid|
    h = Host.new_with_id(hid, client)
    rc = h.info
    next if OpenNebula.is_error?(rc)

    geo = h['TEMPLATE/GEOLOCATION']
    next if geo.nil? || geo.empty?

    # GEOLOCATION can be "lat,lon" or space-separated "lat1,lon1 lat2,lon2"
    # Use the first coordinate pair
    first_coord = geo.split(' ').first
    parts = first_coord.split(',')
    next if parts.length < 2

    lats << parts[0].to_f
    lons << parts[1].to_f
end

if lats.empty?
    puts "No geolocated hosts in cluster #{cluster_id}, skipping centroid"
    exit 0
end

centroid_lat = (lats.sum / lats.length).round(4)
centroid_lon = (lons.sum / lons.length).round(4)

cluster_geo = "GEOLOCATION=\"#{centroid_lat},#{centroid_lon}\""
rc = cluster.update(cluster_geo, true)
raise rc.message if OpenNebula.is_error?(rc)

puts "Cluster #{cluster_id} geolocation centroid: #{centroid_lat},#{centroid_lon} (from #{lats.length} hosts)"

#################################################################
# Carbon intensity via Electricity Maps API
#################################################################

begin
    if File.exist?(ELECTRICITY_MAPS_CONF)
        conf = YAML.safe_load(File.read(ELECTRICITY_MAPS_CONF))
        api_key = conf['api_key'] if conf.is_a?(Hash)
    end

    if api_key.nil? || api_key.empty? || api_key == 'YOUR_API_KEY_HERE'
        puts "No Electricity Maps API key configured, skipping carbon intensity"
        exit 0
    end

    url = URI("https://api.electricitymap.org/v3/carbon-intensity/latest?lat=#{centroid_lat}&lon=#{centroid_lon}")
    http = Net::HTTP.new(url.host, url.port)
    http.use_ssl = true
    http.open_timeout = 10
    http.read_timeout = 10

    request = Net::HTTP::Get.new(url)
    request['auth-token'] = api_key

    response = http.request(request)

    if response.code.to_i == 200
        data = JSON.parse(response.body)
        carbon_intensity = data['carbonIntensity']
        zone = data['zone'] || 'unknown'

        if carbon_intensity
            update_str = "CARBON_INTENSITY=\"#{carbon_intensity}\"\n" \
                         "CARBON_INTENSITY_ZONE=\"#{zone}\"\n" \
                         "CARBON_INTENSITY_UPDATED=\"#{Time.now.utc.iso8601}\""
            rc = cluster.update(update_str, true)
            raise rc.message if OpenNebula.is_error?(rc)

            puts "Cluster #{cluster_id} carbon intensity: #{carbon_intensity} gCO2eq/kWh (zone: #{zone})"
        else
            puts "Carbon intensity not available in API response"
        end
    else
        puts "Electricity Maps API returned #{response.code}: #{response.body}"
    end
rescue => e
    STDERR.puts "Carbon intensity fetch failed: #{e.message}"
end
