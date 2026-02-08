FROM jojo141185/mopidy:release

# Switch to root to install packages
USER root

# Install Mopidy-Autoplay extension to the venv
RUN /opt/venv/bin/pip install --no-cache-dir Mopidy-Autoplay

# Switch back to mopidy user
USER mopidy
