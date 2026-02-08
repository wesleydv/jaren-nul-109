FROM jojo141185/mopidy:release

# Switch to root to install packages
USER root

# Install Mopidy-Autoplay extension to the venv
RUN /opt/venv/bin/pip install --no-cache-dir Mopidy-Autoplay

# Copy config files into the image
COPY mopidy.conf.template /config/mopidy.conf.template
COPY init.sh /config/init.sh
COPY entrypoint.sh /config/entrypoint.sh

# Make scripts executable
RUN chmod +x /config/init.sh /config/entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/config/init.sh"]
CMD ["/opt/venv/bin/mopidy"]

# Switch back to mopidy user
USER mopidy
