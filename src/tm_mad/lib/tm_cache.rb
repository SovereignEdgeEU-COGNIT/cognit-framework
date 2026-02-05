#!/usr/bin/env ruby
#
# frozen_string_literal: true

# ---------------------------------------------------------------------------- #
# Copyright 2002-2025, OpenNebula Project, OpenNebula Systems                  #
#                                                                              #
# Licensed under the Apache License, Version 2.0 (the "License"); you may      #
# not use this file except in compliance with the License. You may obtain      #
# a copy of the License at                                                     #
#                                                                              #
# http://www.apache.org/licenses/LICENSE-2.0                                   #
#                                                                              #
# Unless required by applicable law or agreed to in writing, software          #
# distributed under the License is distributed on an "AS IS" BASIS,            #
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.     #
# See the License for the specific language governing permissions and          #
# limitations under the License.                                               #
# ---------------------------------------------------------------------------- #

require 'fileutils'
require 'json'
require 'open3'
require 'set'
require 'time'
require 'yaml'

module TransferManager

    # CacheManager class to manage local and upstreams caches of images
    class CacheManager

        attr_reader :config

        # CacheManager initialization options
        # @param opts [Hash]
        #   :ds       => Hash, datastore XML
        #   :hostname => String, name of this host
        #   :one_fe   => String, frontend hostname for fallback fetches
        def initialize(config = {})
            ds = config.fetch(:ds_xml)
            defaults = {
                :ds_xml         => ds,
                :cache_path     => ds[:"/DATASTORE/TEMPLATE/CACHE_PATH"] || '/var/lib/one/cache',
                :cache_max_size => (ds[:"/DATASTORE/TEMPLATE/CACHE_MAX_SIZE"] || 10).to_i,
                :min_age        => (ds[:"/DATASTORE/TEMPLATE/CACHE_MIN_AGE"] || 900).to_i,
                :upstreams      => config.fetch(:upstreams),
                :hostname       => config.fetch(:hostname),
                :one_fe         => config.fetch(:one_fe)
            }
            @config = defaults.merge!(config)

            # Validate configuration attributes
            FileUtils.mkdir_p(@config[:cache_path])

            raise ArgumentError, 'cache_path must be a writable directory' unless
                Dir.exist?(@config[:cache_path]) && File.writable?(@config[:cache_path])

            raise ArgumentError, 'max_size must be a positive integer' if
                @config[:cache_max_size] < 0 || @config[:cache_max_size] > 100

            raise ArgumentError, 'upstreams must be an array of strings' unless
                @config[:upstreams].is_a?(Array) &&
                @config[:upstreams].all?(String)

            raise ArgumentError, 'min_age must be a non-negative integer' unless
                @config[:min_age] >= 0

            raise ArgumentError, 'hostname must be a string' if
              @config[:hostname].empty? || @config[:one_fe].empty?

            # Cache for backing file index (backing_file_path -> Set of files using it)
            @backing_file_index = {}
            @backing_file_index_time = nil
            @backing_file_index_ttl = 60 # Refresh index every 60 seconds
        end

        # Retrieve an image from the local or upstream caches.
        #  @param [String] image_id, OpenNebula image filename
        #  @param [String] modtime, last time the image was modified in OpenNebula
        #  @param [String] size, size of the image
        #  @param [String] fe_default, Full source path of the image in the OpenNebula front-end
        #
        # This method attempts to get the image from:
        #   1) The local cache
        #   2) Each configured upstream cache host
        #   3) The frontend if not found in any cache
        #
        # Returns:
        #   - Hash with src (image path) and host if image is available.
        #   - Nil if the image is missing or invalid.
        def get(image_id, modtime, size, fe_default)
            metadataf = metadata_path(image_id)

            return unless cache_lock_block do
                cacheable?(size, image_id)
            end

            lock_block(image_id) do
                # 1) Check local cache
                location = valid?(image_id, modtime)

                if location
                    update_metadata(metadataf, modtime)

                    return location
                end

                @config[:upstreams].each do |upstream|
                    next if upstream == @config[:hostname]

                    location = remote_exec('get', upstream,
                                           :image_id  => image_id,
                                           :modtime   => modtime,
                                           :size      => size,
                                           :fe        => fe_default,
                                           :upstreams => [])
                    next unless location

                    # Found in an upstream cache: fetch into local
                    location = fetch(image_id, modtime, location['src'], location['host'])

                    return location || next
                end

                # 3) Not found locally or in upstreams: fetch from frontend
                fetch(image_id, modtime, fe_default, @config[:one_fe])
            end
        end

        # Fetch an image from a remote host (src) into the local cache.
        #  @param [String] image_id, OpenNebula image filename
        #  @param [String] modtime, Last time the image was modified in OpenNebula
        #  @param [String] location_src, Full source path of the image on the remote host
        #  @param [String] location_host, Hostname where the image is currently available
        #
        # Returns:
        #   - Hash with src (image path) and host if image is available.
        #   - Nil if the image is missing or invalid.
        def fetch(image_id, modtime, location_src, location_host)
            imagef    = image_path(image_id)
            metadataf = metadata_path(image_id)

            scp_cmd = [
                'scp',
                '-o', 'ControlMaster=no',
                '-o', 'ControlPath=none',
                "#{location_host}:#{location_src}",
                imagef
            ].join(' ')

            _out, _err, status = Open3.capture3(scp_cmd)

            unless status.success?
                File.delete(imagef) if File.exist?(imagef)
                File.truncate(metadataf, 0)
                return
            end

            # User manager: Image is available, update metadata
            update_metadata(metadataf, modtime)

            { 'src' => imagef, 'host' => @config[:hostname] }
        end

        # Check if the image exists in the local cache and it is valid.
        # This method assumes the associted lock file is locked.
        #  @param [String] image_id, OpenNebula image id
        #  @param [String] modtime, Last time the image was modified in OpenNebula
        #
        # Returns:
        #   - Hash with src (local cache path) and host (local host) if the image is valid
        #   - nil if the image is missing or invalid
        def valid?(image_id, modtime)
            imagef    = image_path(image_id)
            metadataf = metadata_path(image_id)

            return unless File.exist?(imagef)
            return if File.empty?(metadataf)

            image_metadata = YAML.load_file(metadataf)

            return unless modtime == image_metadata[:modtime]

            { 'src' => imagef, 'host' => @config[:hostname] }
        rescue StandardError
            return
        end

        # Update the `last_used` timestamp in the image's metadata file.
        # This method assumes the associted lock file is locked.
        #  @param [String] metadataf, Path to the metadata file to update.
        #  @param [String] modtime, Last time the image was modified in OpenNebula
        def update_metadata(metadataf, modtime)
            meta = YAML.load_file(metadataf) rescue {}

            meta[:last_used] = Time.now.iso8601
            meta[:modtime]   = modtime

            File.write(metadataf, meta.to_yaml)
        end

        # Determine available cache space.
        # This method assumes the cache manager lock is locked.
        #
        # @return [Array(Integer, Integer)] Returns:
        #   - available_cache: Space available for new images
        #   - absolute_cache: Max usable cache size based on cache_max_size
        def cache_space
            # Host space
            df_out = `df -P --block-size=1 '#{@config[:cache_path]}'`

            _, total_str, _, avail_str, = df_out.lines.last.split

            total_fs  = total_str.to_i
            free_disk = avail_str.to_i

            # Cache space
            du_out     = `du -sb '#{@config[:cache_path]}'`
            used_cache = du_out.split.first.to_i

            # Get the minimum between:
            # - the total free disk space (in bytes)
            # - the cache available size (in bytes)
            absolute_cache = (total_fs * @config[:cache_max_size].to_i / 100.0).floor
            free_cache     = absolute_cache - used_cache

            available_cache = [free_cache, free_disk].min

            [available_cache, absolute_cache]
        end

        # Select LRU images (whose `last_used` is older than `min_age``).
        # This method assumes the cache manager lock  is locked.
        #  @param [Integer] file_size, Size of the incoming image
        #  @param [Integer] available_cache, Current available space
        #
        # @return [Array<String>] List of image_ids to delete
        def lru_policy(file_size, image_id, available_cache)
            metadata_files = Dir.glob(File.join(@config[:cache_path], '*-metadata'))
                                .sort_by {|f| File.mtime(f) }

            threshold = Time.now - @config[:min_age].to_i

            selected  = []
            free_size = 0

            metadata_files.each do |metadataf|
                meta = YAML.load_file(metadataf)
                next if Time.parse(meta[:last_used]) >= threshold

                imagef = metadataf.sub(/-metadata$/, '')
                next unless File.exist?(imagef)

                id = File.basename(imagef)

                next if id == image_id

                # Check if image is used as backing file - protect it from eviction
                if is_backing_file_in_use?(imagef)
                    STDERR.puts "Skipping eviction of #{id}: image is used as backing file"
                    next
                end

                selected << id
                free_size += File.size(imagef)

                return selected if (free_size + available_cache) >= file_size
            end

            []
        end

        # Attempts to make room for a new image of `size` bytes.
        #  @param [Integer] size, Size of the incoming image
        #  @param [String] image_id, OpenNebula image id
        #
        # - Returns true if there is now (or already was) enough space.
        # - Returns false if `size` is too large or if eviction is not enough.
        def cacheable?(size, image_id)
            available_cache, absolute_cache = cache_space

            return false if size > absolute_cache

            # Already enough free space (no eviction needed)
            return true if size <= available_cache

            # Need to evict LRU candidates
            candidates = lru_policy(size, image_id, available_cache)

            candidates.each do |candidate_id|
                delete_image(candidate_id)
            end

            return true if valid?(image_id)

            return false if candidates.empty?

            # Make sure we have at least 1GB left
            new_available_cache, = cache_space
            new_available_cache >= 1 * 1024 * 1024 * 1024 # 1GB
        end

        # Perform a remote cache operation via SSH.
        #  @param [String] op, Operation to perform (e.g., 'get')
        #  @param [String] host, Target remote host
        #  @param [Hash] kwargs, Keyword arguments for the operation
        #
        # Returns:
        #   - Hash: Parsed JSON response if successful
        #   - nil: If the operation failed or the result was empty
        def remote_exec(op, host, **args)
            cmd = case op
                  when 'get'
                      config = @config.clone

                      config[:hostname]  = host
                      config[:upstreams] = args[:upstreams]

                      config_json = JSON.dump(config)

                      <<~CMD
                          ruby #{__FILE__} '#{op}' '#{config_json}' \
                          '#{args[:image_id]}' '#{args[:modtime]}' \
                          '#{args[:size]}' '#{args[:fe]}'
                      CMD
                  else
                      raise "Unknown operation: #{op}"
                  end

            ssh_cmd = [
                'ssh', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
                '-o', 'ForwardAgent=yes', host, cmd
            ]

            out, _err, status = Open3.capture3(*ssh_cmd)

            return unless status.success?
            return if out.nil? || out.empty?

            JSON.parse(out)
        end

        # ----------------------------------------------------------------------
        # Backing file protection methods
        # ----------------------------------------------------------------------

        # Check if a cached image is used as backing file by any VM on this host
        # Uses a reverse index for efficient lookup (O(1) instead of scanning all VMs)
        # @param [String] image_path Full path to the cached image file
        # @return [Boolean] true if image is used as backing file, false otherwise
        def is_backing_file_in_use?(image_path)
            return false unless File.exist?(image_path)

            # Refresh index if needed
            refresh_backing_file_index if backing_file_index_expired?

            begin
                # Normalize path for comparison (resolve symlinks, etc.)
                normalized_path = File.realpath(image_path)
            rescue StandardError
                # If we can't resolve the path, use it as-is
                normalized_path = File.expand_path(image_path)
            end

            # Fast lookup in the index
            @backing_file_index.key?(normalized_path) && !@backing_file_index[normalized_path].empty?
        rescue StandardError => e
            # Log error but don't block eviction if check fails
            STDERR.puts "Error checking if image is in use: #{e.message}"
            false
        end

        # Build a reverse index: backing_file_path -> Set of files using it
        # This allows O(1) lookup instead of scanning all VMs for each cached image
        def refresh_backing_file_index
            @backing_file_index = {}
            system_ds_paths = find_system_datastore_paths

            system_ds_paths.each do |ds_path|
                next unless Dir.exist?(ds_path)

                begin
                    # Scan all VM directories
                    Dir.glob(File.join(ds_path, '*')).each do |vm_dir|
                        next unless File.directory?(vm_dir)

                        # Check all disk files in VM directory
                        Dir.glob(File.join(vm_dir, 'disk.*')).each do |disk_path|
                            scan_disk_for_backing_files(disk_path)
                        end
                    end
                rescue StandardError => e
                    # Log error but continue checking other datastores
                    STDERR.puts "Error scanning datastore #{ds_path}: #{e.message}"
                end
            end

            @backing_file_index_time = Time.now
        end

        # Scan a disk path and add its backing file to the index
        # @param [String] disk_path Path to disk file (may be symlink)
        def scan_disk_for_backing_files(disk_path)
            files_to_check = []

            # Add main disk file
            if File.symlink?(disk_path)
                begin
                    actual = File.readlink(disk_path)
                    actual = File.join(File.dirname(disk_path), actual) unless File.absolute_path?(actual)
                    files_to_check << actual if File.exist?(actual)
                rescue StandardError
                    # If symlink is broken, skip it
                end
            elsif File.file?(disk_path)
                files_to_check << disk_path
            end

            # Add all snapshot files
            snap_dir = "#{disk_path}.snap"
            if Dir.exist?(snap_dir)
                begin
                    files_to_check.concat(Dir.glob(File.join(snap_dir, '*')))
                rescue StandardError
                    # If we can't read snap directory, continue
                end
            end

            files_to_check.each do |file|
                next unless File.file?(file)

                backing_file = get_backing_file(file)
                next unless backing_file

                begin
                    normalized_backing = File.realpath(backing_file)
                rescue StandardError
                    normalized_backing = File.expand_path(backing_file)
                end

                # Add to index: backing_file -> Set of files using it
                @backing_file_index[normalized_backing] ||= Set.new
                @backing_file_index[normalized_backing].add(file)
            end
        end

        # Check if the backing file index has expired and needs refresh
        # @return [Boolean] true if index is expired or doesn't exist
        def backing_file_index_expired?
            return true if @backing_file_index_time.nil?

            (Time.now - @backing_file_index_time) > @backing_file_index_ttl
        end


        # Get backing file path from a qcow2 image
        # @param [String] qcow2_path Path to qcow2 image file
        # @return [String, nil] Backing file path or nil if no backing file
        def get_backing_file(qcow2_path)
            return nil unless File.exist?(qcow2_path)

            begin
                # Use qemu-img info to get backing file
                info_cmd = "qemu-img info -U --output json '#{qcow2_path}' 2>/dev/null"
                info_json = `#{info_cmd}`.strip

                return nil if info_json.empty?

                info = JSON.parse(info_json)

                # Check for full-backing-filename (absolute path) or backing-filename (relative)
                backing = info['full-backing-filename'] || info['backing-filename']

                return nil unless backing && backing != 'null'

                # If relative path, make it absolute
                if backing.start_with?('/')
                    backing
                else
                    File.join(File.dirname(qcow2_path), backing)
                end
            rescue StandardError
                nil
            end
        end

        # Find system datastore paths on this host
        # @return [Array<String>] List of system datastore paths
        def find_system_datastore_paths
            paths = []

            # Get DATASTORE_LOCATION from environment or use default
            datastore_base = ENV['DATASTORE_LOCATION'] || '/var/lib/one/datastores'

            # Default system datastore (ID 0) - most common case
            default_path = File.join(datastore_base, '0')
            paths << default_path if Dir.exist?(default_path)

            # Check for other system datastores (could be multiple)
            if Dir.exist?(datastore_base)
                begin
                    Dir.glob(File.join(datastore_base, '*')).each do |ds_path|
                        next unless File.directory?(ds_path)
                        next if paths.include?(ds_path) # Skip if already added

                        # Check if this looks like a system datastore (has VM directories)
                        # System datastores typically have numeric subdirectories (VM IDs)
                        has_vm_dirs = begin
                            Dir.glob(File.join(ds_path, '*')).any? do |entry|
                                next unless File.directory?(entry)

                                vm_id = File.basename(entry)
                                # VM directories are numeric IDs
                                if vm_id.match?(/^\d+$/)
                                    # Check if it contains disk files (indicates it's a VM directory)
                                    Dir.glob(File.join(entry, 'disk.*')).any? ||
                                        Dir.glob(File.join(entry, '*.snap')).any?
                                else
                                    false
                                end
                            end
                        rescue StandardError
                            false
                        end

                        paths << ds_path if has_vm_dirs
                    end
                rescue StandardError => e
                    STDERR.puts "Error scanning datastore base #{datastore_base}: #{e.message}"
                end
            end

            # Fallback: if no paths found, use default if it exists
            paths = [default_path] if paths.empty? && Dir.exist?(default_path)

            paths
        end

        # ----------------------------------------------------------------------
        # Lock and file helpers
        # ----------------------------------------------------------------------

        #  @return [String] path to the image in the cache
        def image_path(image_id)
            File.join(@config[:cache_path], image_id.to_s)
        end

        #  @return [String] path to the metadata file in the cache
        def metadata_path(image_id)
            File.join(@config[:cache_path], "#{image_id}-metadata")
        end

        #  @return [String] path to the image lock file in the cache
        def lock_path(image_id)
            File.join(@config[:cache_path], "#{image_id}-lock")
        end

        # Delete the image, metadata and lock file from the cache
        def delete_image(id)
            [image_path(id), metadata_path(id), lock_path(id)].each do |f|
                File.delete(f) if File.exist? f
            end
        end

        # Execute code with an exclusive lock on an image-specific lock.
        def lock_block(image_id, *args, &block)
            l_path = lock_path(image_id)

            File.open(l_path, 'w') do |file|
                file.flock(File::LOCK_EX)

                block.call(*args)
            end
        end

        # Execute code with an exclusive lock on the global cache lock.
        def cache_lock_block(*args, &block)
            l_path = File.join(@config[:cache_path], '.lock')

            File.open(l_path, 'w') do |file|
                file.flock(File::LOCK_EX)

                block.call(*args)
            end
        end

    end

end

#-------------------------------------------------------------------------------
# Main program
#-------------------------------------------------------------------------------
if __FILE__ == $PROGRAM_NAME
    operation = ARGV.shift
    config    = JSON.parse(ARGV.shift, :symbolize_names => true)

    cache = TransferManager::CacheManager.new(config)

    r = case operation
        when 'get'
            image_id, modtime, size, fe_default = ARGV
            cache.get(image_id, modtime, size.to_i, fe_default)
        else
            raise "Unknown method: #{method}"
        end

    puts r.to_json
    exit 0
end
